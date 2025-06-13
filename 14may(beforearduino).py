from flask import Flask, request, render_template, jsonify
import pandas as pd
import cups
import tempfile
import os
import logging
from datetime import datetime
import json

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False  # Maintain JSON key order
df_cache = None  # Cache for the uploaded DataFrame

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def send_to_printer(zpl_code, printer_name=None):
    """Send raw ZPL code to the printer using CUPS"""
    try:
        conn = cups.Connection()
        printers = conn.getPrinters()
        
        if not printers:
            logger.error("No printers found")
            return False, "No printers found"

        if printer_name is None:
            printer_name = list(printers.keys())[0]
            logger.info(f"Using default printer: {printer_name}")

        # Create a temporary file
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.zpl',
            delete=False,
            encoding='utf-8'
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(zpl_code)
            logger.debug(f"Created temp ZPL file at {temp_path}")

        try:
            # Print with raw option
            print_options = {
                'raw': '1',
                'document-format': 'application/vnd.zebra-zpl'
            }
            job_id = conn.printFile(
                printer_name,
                temp_path,
                f"Zebra_Label_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                print_options
            )
            logger.info(f"Sent print job to {printer_name}, job ID: {job_id}")
            return True, f"Label sent to {printer_name} (Job ID: {job_id})"
        except cups.IPPError as ippe:
            logger.error(f"CUPS IPP Error: {ippe}")
            return False, f"Printer error: {ippe}"
        finally:
            try:
                os.unlink(temp_path)
                logger.debug(f"Deleted temp file {temp_path}")
            except Exception as e:
                logger.warning(f"Could not delete temp file: {e}")

    except Exception as e:
        logger.error(f"Printing failed: {str(e)}", exc_info=True)
        return False, f"Printing failed: {str(e)}"

def generate_sticker_zpl(record):
    """Generate ZPL code for the 4x3 label with dynamic data"""
    # Validate record is a dictionary
    if not isinstance(record, dict):
        raise ValueError("Record must be a dictionary")
    
    # Set default values for all required fields
    defaults = {
        'GEN_ATTRIBUTE_VALUE5': 'N/A',  # Replaces HID_Woven
        'LOT_NUMBER': 'N/A',
        'DISPLAY_ITEM_NUMBER': 'N/A',
        'ASN_LINE_NUMBER': 'N/A',
        'QUANTITY': 'N/A',
        'GEN_ATTRIBUTE_VALUE7': 'N/A',  # Replaces "30" with the L + Need 1YDS"
        'SUPPLIER_HU': 'N/A',
        'GEN_ATTRIBUTE_VALUE8': 'N/A',  # Width value
        'GEN_ATTRIBUTE_VALUE2': 'N/A',  # Tally value
        'PO_LINE_NUMBER': 'N/A',
        'HU_ID': 'N/A',
        'VENDOR_CODE': 'N/A'  # Replaces "INS"
    }
    
    # Merge record with defaults
    merged_record = {**defaults, **record}
    
    # 4x3 label ZPL template with all requested changes
    zpl_template = """^XA
^PW812
^LL609
^FS

^CF0,30

^FO600,20^A0N,28,28^FD1 YDS, 99/99^FS

^FO50,50^A0N,35,35^FD{GEN_ATTRIBUTE_VALUE5}^FS
^FO50,90^A0N,28,28^FDLOT: {LOT_NUMBER}^FS
^FO50,130^A0N,28,28^FDMaterial Ref    {DISPLAY_ITEM_NUMBER}^FS
^FO50,170^A0N,28,28^FDColor    Mat    {ASN_LINE_NUMBER} MTR^FS
^FO50,210^A0N,28,28^FDQty  {QUANTITY}  YD  {GEN_ATTRIBUTE_VALUE7}^FS
^FO50,250^A0N,28,28^FDV Roll  {SUPPLIER_HU}  Width  {GEN_ATTRIBUTE_VALUE8}^FS
^FO50,290^A0N,28,28^FD^FS
^FO50,310^A0N,28,28^FDTag Qty^FS
^FO50,340^A0N,28,28^FD{PO_LINE_NUMBER}^FS
^FO50,370^A0N,28,28^FDTally :{GEN_ATTRIBUTE_VALUE2}    Invoice No^FS
^FO50,400^A0N,28,28^FD^FS
^FO50,420^A0N,28,28^FD{HU_ID}{VENDOR_CODE}^FS

^FO50,460^BY2
^BCN,80,Y,N,N
^FD{HU_ID}^FS

^FO400,460
^BQN,2,4
^FDLA,{HU_ID}^FS

^XZ
"""
    
    try:
        return zpl_template.format(**merged_record)
    except KeyError as e:
        logger.error(f"Missing required field in record: {e}")
        raise ValueError(f"Missing required field: {e}")
    
    
@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload():
    global df_cache
    if 'file' not in request.files:
        return "No file uploaded", 400
        
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    if file and file.filename.lower().endswith(('.xlsx', '.xls')):
        try:
            df_cache = pd.read_excel(file).fillna('N/A')
            columns = [str(col) for col in df_cache.columns.tolist()]
            logger.info(f"Uploaded file with {len(df_cache)} rows and columns: {columns}")
            return render_template('form.html', columns=columns)
        except Exception as e:
            logger.error(f"File processing failed: {str(e)}", exc_info=True)
            return f"Error processing file: {str(e)}", 400
    return "Invalid file type. Please upload an Excel file.", 400

@app.route('/suggest/<field>')
def suggest(field):
    if df_cache is not None and field in df_cache.columns:
        values = df_cache[field].dropna().astype(str).unique().tolist()
        return jsonify(values)
    return jsonify([])

@app.route('/filter')
def filter_records():
    if df_cache is None:
        return jsonify([])

    filters = {
        'SUPPLIER_HU': request.args.get('SUPPLIER_HU', ''),
        'LOT_NUMBER': request.args.get('LOT_NUMBER', ''),
        'QUANTITY': request.args.get('QUANTITY', ''),
        'Color': request.args.get('Color', '')
    }

    filtered_df = df_cache.copy()
    for field, value in filters.items():
        if field in filtered_df.columns and value:
            filtered_df = filtered_df[
                filtered_df[field].astype(str).str.contains(
                    value, case=False, na=False
                )
            ]

    return jsonify(filtered_df.to_dict(orient='records'))

@app.route('/print-sticker', methods=['POST'])
def print_sticker():
    try:
        # Get JSON data from request
        data = request.get_json()
        if not data or 'data' not in data:
            raise ValueError("No data provided in request")
        
        record = data['data']
        logger.info(f"Print request received for record: {record}")
        
        # Validate record data type
        if isinstance(record, str):
            try:
                # If it's a string, try to parse it as JSON
                record = json.loads(record)
            except json.JSONDecodeError:
                raise ValueError("Invalid data format - could not parse JSON string")
        
        if not isinstance(record, dict):
            raise TypeError(f"Expected dictionary, got {type(record).__name__}")
        
        # Generate ZPL code
        zpl_code = generate_sticker_zpl(record)
        logger.debug(f"Generated ZPL code:\n{zpl_code}")
        
        # Send to printer
        success, message = send_to_printer(zpl_code)
        if not success:
            raise RuntimeError(message)
            
        return jsonify({
            "success": True,
            "message": message,
            "zpl_length": len(zpl_code)
        })

    except Exception as e:
        logger.error(f"Print sticker failed: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 400

def get_available_printers():
    try:
        conn = cups.Connection()
        return conn.getPrinters()
    except Exception as e:
        logger.error(f"Failed to get printers: {str(e)}")
        return {}

if __name__ == '__main__':
    # Log available printers at startup
    printers = get_available_printers()
    if printers:
        logger.info(f"Available printers: {list(printers.keys())}")
    else:
        logger.warning("No printers found or printer service not available")

    app.run(
        host='0.0.0.0',
        port=5001,
        debug=True,
        threaded=True
    )