from flask import Flask, request, render_template, jsonify
import pandas as pd
import cups
import tempfile
import os
import logging
from datetime import datetime
import json
import threading
import serial
import time
from threading import Lock

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Serial setup
SERIAL_PORT = '/dev/tty.usbmodem101'
BAUD_RATE = 9600
ser = None
latest_distance = "0.00"
latest_distance_cm = "0.00"  # Add this
latest_distance_in = "0.00"

# Update your serial_worker function with better error handling
def serial_worker():
    global ser, latest_distance, latest_distance_cm, latest_distance_in
    while True:
        try:
            if ser is None or not ser.is_open:
                try:
                    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
                    time.sleep(2)
                    ser.reset_input_buffer()
                except serial.SerialException as e:
                    print(f"Failed to open serial port: {e}")
                    ser = None
                    time.sleep(2)
                    continue
            
            try:
                if ser.in_waiting:
                    line = ser.readline().decode('utf-8').strip()
                    if "Distance:" in line:
                        parts = line.split("Distance:")
                        if len(parts) > 1:
                            units_part = parts[1]
                            mm_part = units_part.split("mm")[0].strip()
                            latest_distance = mm_part

                            if "cm |" in units_part:
                                cm_part = units_part.split("cm |")[0].split()[-1].strip()
                                latest_distance_cm = cm_part

                            if "in" in units_part:
                                in_part = units_part.split("in")[0].split()[-1].strip()
                                latest_distance_in = in_part

                            print(f"Updated measurements - mm: {latest_distance}, cm: {latest_distance_cm}, in: {latest_distance_in}")
            except UnicodeDecodeError:
                ser.reset_input_buffer()
                continue
            except serial.SerialException as e:
                print(f"Serial read error: {e}")
                ser.close()
                ser = None
                time.sleep(2)
                continue

            time.sleep(0.1)

        except Exception as e:
            print(f"Unexpected error in serial worker: {e}")
            if ser:
                try:
                    ser.close()
                except:
                    pass
                ser = None
            time.sleep(2)

# Start serial thread
serial_thread = threading.Thread(target=serial_worker, daemon=True)
serial_thread.start()

@app.route('/get-length')
def get_length():
    try:
        return jsonify({
            'length_in': latest_distance_in,
            'status': 'active' if float(latest_distance) > 0 else 'idle'
        })
    except ValueError:
        return jsonify({
            
            'length_in': '0.00',
            'status': 'error'
        })


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
    defaults = {
        'GEN_ATTRIBUTE_VALUE5': 'N/A',
        'LOT_NUMBER': 'N/A',
        'DISPLAY_ITEM_NUMBER': 'N/A',
        'ASN_LINE_NUMBER': 'N/A',
        'QUANTITY': 'N/A',
        'GEN_ATTRIBUTE_VALUE7': 'N/A',
        'SUPPLIER_HU': 'N/A',
        'GEN_ATTRIBUTE_VALUE8  With': latest_distance,
        'GEN_ATTRIBUTE_VALUE2': 'N/A',
        'PO_LINE_NUMBER': 'N/A',
        'HU_ID': 'N/A',
        'VENDOR_CODE': 'N/A',
        'GEN_ATTRIBUTE_VALUE9': '',
        'Color': 'N/A'
    }

    data = {**defaults, **record}
    
    try:
        # Use inches for printing
        width_inches = latest_distance_in
    except ValueError:
        width_inches = "0.00"
    
    data['GEN_ATTRIBUTE_VALUE8  With'] = width_inches

    zpl = f"""
   ^XA
    ^PW812
    ^LL750
    ^CF0,30

    ^FO600,20^A0N,28,28^FD{data.get('GEN_ATTRIBUTE_VALUE9', '')},^FS
    ^FO150,50^A0N,40,45^FD{data.get('GEN_ATTRIBUTE_VALUE5', '')}^FS
    ^FO50,90^A0N,30,32^FDLOT: {data.get('LOT_NUMBER', '')}^FS
    ^FO50,130^A0N,30,30^FDMaterial Ref {data.get('DISPLAY_ITEM_NUMBER', '')}^FS

    ^FO40,170^GB740,150,3^FS  ; Outer main box

    ^FO40,220^GB740,0,2^FS    ; Row 1 bottom
    ^FO40,260^GB740,0,2^FS    ; Row 2 bottom

    ; Row 1 - Mat | Color | MTR
    ^FO50,180^A0N,32,32^FDMat^FS
    ^FO180,180^A0N,31,31^FD{data.get('Color', '')}^FS
    ^FO420,180^A0N,32,32^FD{data.get('ASN_LINE_NUMBER', '')}^FS

    ^FO160,170^GB0,50,2^FS
    ^FO396,170^GB0,50,2^FS

    ; Row 2 - Qty | Value | YD | Size
    ^FO50,230^A0N,28,28^FDQty^FS
    ^FO150,230^A0N,28,28^FD{data.get('QUANTITY', '')}^FS
    ^FO320,230^A0N,28,28^FDYD^FS
    ^FO375,230^A0N,28,28^FD{data.get('GEN_ATTRIBUTE_VALUE7', '')}^FS

    ^FO130,220^GB0,40,2^FS
    ^FO290,220^GB0,40,2^FS
    ^FO370,220^GB0,40,2^FS

    ; Row 3 - V Roll | HU | Width
    ^FO50,270^A0N,35,32^FDV Roll^FS
    ^FO180,275^A0N,35,32^FD{data.get('SUPPLIER_HU', '')}^FS
    ^FO540,275^A0N,35,32^FDWidth^FS
    ^FO650,275^A0N,35,32^FD{data.get('GEN_ATTRIBUTE_VALUE8  With', '')}^FS  ; Fixed line

    ^FO150,260^GB0,50,2^FS
    ^FO510,260^GB0,50,2^FS

    ; New middle section layout (4x3)
    ^FO30,325^BQN,2,3^FDLA,{data.get('HU_ID', '')}^FS  ; Small QR top left
    ^FO110,325^BCN,50,Y,N,N^FD{data.get('HU_ID', '')}^FS  ; Barcode next to QR
    ^FO105,420^A0N,28,28^FDTally: {data.get('GEN_ATTRIBUTE_VALUE2', '')}^FS  ; Tally below QR and barcode
        ^FO475,325^BQN,2,4^FDLA,{data.get('HU_ID', '')}^FS  ; Additional QR in the middle

    ^FO573,325^A0N,28,28^FDTag Qty^FS  ; Tag Qty label top middle
    ^FO575,360^A0N,28,28^FD{data.get('PO_LINE_NUMBER', '')}^FS  ; Tag Qty value below
    
    ^FO665,325^BQN,2,5^FDLA,{data.get('HU_ID', '')}^FS  ; Big QR right corner

    ; Bottom Box
     ; Enlarged Bottom Box moved closer to Tally
    ^FO30,450^GB500,140,4^FS  ; Bigger box (100 height instead of 80) moved up (450 instead of 520)
    ^FO50,468^A0N,145,115^FD {data.get('HU_ID', '')}^FS  ; Bigger font (45 instead of 38)

    ; Vendor Code - placed to the right of the bottom box
    ^FO538,470^A0N,130,45^FD{data.get('Vendor_code', '')}^FS  ; Bigger font to match

    ^XZ
    """
    return zpl

    
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
    
@app.route('/print-multiple-stickers', methods=['POST'])
def print_multiple_stickers():
    try:
        data = request.get_json()
        records = data.get('records', [])
        
        if not records:
            return jsonify({"success": False, "message": "No records provided"}), 400
        
        for record in records:
            # Your existing print logic for each record
            print_sticker(record)  # Replace with your actual print function
            
        return jsonify({
            "success": True,
            "message": f"Successfully printed {len(records)} stickers"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error printing stickers: {str(e)}"
        }), 500


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