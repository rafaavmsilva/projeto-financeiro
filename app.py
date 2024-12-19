from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
import pandas as pd
import sqlite3
import os
import json
from werkzeug.utils import secure_filename
import threading
import uuid
from functools import wraps
from auth_client import AuthClient
from cnpj_handler import CNPJHandler
from transaction_handler import TransactionHandler

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Initialize handlers
auth_client = AuthClient(
    auth_server_url=os.getenv('AUTH_SERVER_URL', 'https://af360bank.onrender.com'),
    app_name=os.getenv('APP_NAME', 'financeiro')
)
cnpj_handler = CNPJHandler()
transaction_handler = TransactionHandler()

# Global dictionary to store upload progress
upload_progress = {}

@app.before_first_request
def initialize():
    init_db()

def ensure_upload_folder():
    folder = app.config['UPLOAD_FOLDER']
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
        col_str = str(col).upper().strip()
        for name in column_names:
            if name.upper() in col_str:
                return col
    return None

def process_file_with_progress(filepath, process_id):
    try:
        df = pd.read_excel(filepath, skiprows=1)  # Skip header row
        upload_progress[process_id].update({
            'total': len(df),
            'message': 'Processing file...'
        })

        data_col = find_matching_column(df, ['Data', 'DATE', 'DT', 'AGENCIA', 'DATA'])
        desc_col = find_matching_column(df, ['Histórico', 'HISTORIC', 'DESCRIÇÃO', 'DESCRICAO', 'CONTA', 'HISTORICO'])
        valor_col = find_matching_column(df, ['Valor', 'VALUE', 'QUANTIA', 'VALOR', 'VLR'])

        # Add debug logging
        if not all([data_col, desc_col, valor_col]):
            print("Column detection results:")
            print(f"Available columns: {df.columns.tolist()}")
            print(f"Data column: {data_col}")
            print(f"Description column: {desc_col}")
            print(f"Value column: {valor_col}")
            raise Exception("Required columns not found")

        conn = get_db_connection()
        cursor = conn.cursor()

        init_db()

        for index, row in df.iterrows():
            upload_progress[process_id]['current'] = index + 1
            
            try:
                try:
                    date = pd.to_datetime(row[data_col], format='%d/%m/%Y').date()
                except:
                    try:
                        date = pd.to_datetime(row[data_col]).date()
                    except:
                        print(f"Error processing date at row {index}: {row[data_col]}")
                        continue

                description = str(row[desc_col]).strip()
                value_str = str(row[valor_col])
                value_str = value_str.replace('R$', '').replace(' ', '').strip()
                if ',' in value_str and '.' in value_str:
                    value_str = value_str.replace('.', '').replace(',', '.')
                elif ',' in value_str:
                    value_str = value_str.replace(',', '.')
                value = float(value_str)

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
        # Ensure upload directory exists
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
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

@app.route('/upload_progress/<process_id>')
@login_required
def get_upload_progress(process_id):
    if process_id in upload_progress:
        progress = upload_progress[process_id]
        # Remove completed processes to free up memory
        if progress.get('status') in ['completed', 'error']:
            del upload_progress[process_id]
        return jsonify(progress)
    return jsonify({'status': 'not_found'})

@app.route('/recebidos')
@login_required
def recebidos():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM transactions 
        WHERE type = 'CREDITO' 
        ORDER BY date DESC
    ''')
    transactions = cursor.fetchall()
    conn.close()
    
    return render_template('index.html', 
                         transactions=transactions, 
                         active_page='recebidos')

@app.route('/enviados')
@login_required
def enviados():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM transactions 
        WHERE type = 'DEBITO' 
        ORDER BY date DESC
    ''')
    transactions = cursor.fetchall()
    conn.close()
    
    return render_template('index.html', 
                         transactions=transactions, 
                         active_page='enviados')

@app.route('/transactions')
@login_required
def transactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM transactions ORDER BY date DESC')
    transactions = cursor.fetchall()
    conn.close()
    
    return render_template('index.html', 
                         transactions=transactions, 
                         active_page='transactions')

@app.route('/transactions_summary')
@login_required
def transactions_summary():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            strftime('%Y-%m', date) as month,
            SUM(CASE WHEN type = 'CREDITO' THEN value ELSE 0 END) as total_credits,
            SUM(CASE WHEN type = 'DEBITO' THEN value ELSE 0 END) as total_debits,
            COUNT(CASE WHEN type = 'CREDITO' THEN 1 END) as credit_count,
            COUNT(CASE WHEN type = 'DEBITO' THEN 1 END) as debit_count
        FROM transactions 
        GROUP BY strftime('%Y-%m', date)
        ORDER BY month DESC
    ''')
    summary = cursor.fetchall()
    conn.close()
    
    return render_template('index.html', 
                         transactions_summary=summary, 
                         active_page='transactions_summary')

@app.route('/cnpj_verification', methods=['GET', 'POST'])
@login_required
def cnpj_verification():
    if request.method == 'POST':
        cnpj = request.form.get('cnpj')
        if cnpj:
            company_info = cnpj_handler.get_company_info(cnpj)
            if company_info:
                return render_template('index.html',
                                    active_page='cnpj_verification',
                                    company_info=company_info)
            else:
                flash('CNPJ não encontrado ou serviço indisponível', 'error')
    
    return render_template('index.html',
                         active_page='cnpj_verification')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)