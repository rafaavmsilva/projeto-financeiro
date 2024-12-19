from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from datetime import datetime, timedelta
import sqlite3
import os
import pandas as pd
from werkzeug.utils import secure_filename
import threading
import uuid
from functools import wraps
from auth_client import AuthClient
from cnpj_handler import CNPJHandler
from transaction_handler import TransactionHandler

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

# Initialize handlers
cnpj_handler = CNPJHandler()
transaction_handler = TransactionHandler()

# Initialize AuthClient
auth_client = AuthClient(
    auth_server_url=os.getenv('AUTH_SERVER_URL', 'https://af360bank.onrender.com'),
    app_name=os.getenv('APP_NAME', 'financeiro')
)
auth_client.init_app(app)

# Global variables
upload_progress = {}

# Create necessary directories
for folder in ['instance', 'uploads']:
    if not os.path.exists(folder):
        os.makedirs(folder)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = session.get('token')
        if not token:
            return redirect('https://af360bank.onrender.com/login')
        
        verification = auth_client.verify_token(token)
        if not verification or not verification.get('valid'):
            session.clear()
            return redirect('https://af360bank.onrender.com/login')
        
        return f(*args, **kwargs)
    return decorated_function

def get_db_connection():
    os.makedirs('instance', exist_ok=True)
    conn = sqlite3.connect('instance/financas.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            description TEXT NOT NULL,
            value REAL NOT NULL,
            type TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            document TEXT
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_document ON transactions(document)')
    
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'xls', 'xlsx'}

def find_matching_column(df, column_names):
    for col in df.columns:
        if col.upper() in [name.upper() for name in column_names]:
            return col
    return None

def process_file_with_progress(filepath, process_id):
    try:
        df = pd.read_excel(filepath)
        total_rows = len(df)
        upload_progress[process_id].update({
            'total': total_rows,
            'message': 'Processing file...'
        })

        # Find columns
        data_col = find_matching_column(df, ['Data', 'DATE', 'DT', 'AGENCIA'])
        desc_col = find_matching_column(df, ['Histórico', 'HISTORIC', 'DESCRIÇÃO', 'DESCRICAO', 'CONTA'])
        valor_col = find_matching_column(df, ['Valor', 'VALUE', 'QUANTIA', 'Unnamed: 4'])

        if not all([data_col, desc_col, valor_col]):
            raise Exception("Required columns not found")

        conn = get_db_connection()
        cursor = conn.cursor()

        for index, row in df.iterrows():
            upload_progress[process_id]['current'] = index + 1
            
            try:
                date = pd.to_datetime(row[data_col]).date()
                description = str(row[desc_col]).strip()
                value = float(str(row[valor_col]).replace('R$', '').replace('.', '').replace(',', '.'))

                if pd.isna(date) or pd.isna(description) or pd.isna(value):
                    continue

                transaction_type = transaction_handler.detect_type(description, value)
                enriched_description = cnpj_handler.extract_and_enrich_cnpj(description, transaction_type)

                cursor.execute('''
                    INSERT INTO transactions (date, description, value, type, transaction_type)
                    VALUES (?, ?, ?, ?, ?)
                ''', (date, enriched_description, value, 'CREDITO' if value > 0 else 'DEBITO', transaction_type))

            except Exception as e:
                print(f"Error processing row {index}: {str(e)}")
                continue

        conn.commit()
        conn.close()

        upload_progress[process_id]['status'] = 'completed'
        upload_progress[process_id]['message'] = 'Processing completed'

    except Exception as e:
        upload_progress[process_id].update({
            'status': 'error',
            'message': str(e)
        })
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

@app.route('/')
@login_required
def index():
    return render_template('index.html', active_page='index')

@app.route('/auth')
def auth():
    token = request.args.get('token')
    if not token:
        return redirect('https://af360bank.onrender.com/login')
    
    verification = auth_client.verify_token(token)
    if not verification or not verification.get('valid'):
        return redirect('https://af360bank.onrender.com/login')
    
    session['token'] = token
    session['authenticated'] = True
    session.permanent = True
    
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file selected'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'})
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        file.save(filepath)
        
        process_id = str(uuid.uuid4())
        upload_progress[process_id] = {
            'status': 'processing',
            'current': 0,
            'total': 0,
            'message': 'Starting process...'
        }
        
        thread = threading.Thread(target=process_file_with_progress, args=(filepath, process_id))
        thread.start()
        
        return jsonify({
            'success': True,
            'process_id': process_id,
            'message': 'File uploaded and being processed'
        })
    
    return jsonify({'success': False, 'message': 'Invalid file type'})

@app.route('/progress/<process_id>')
def get_progress(process_id):
    if process_id in upload_progress:
        return jsonify(upload_progress[process_id])
    return jsonify({'status': 'not_found'})

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)