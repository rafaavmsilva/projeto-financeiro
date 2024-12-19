from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
import pandas as pd
import sqlite3
import os
import json
from werkzeug.utils import secure_filename
import threading
import uuid
from functools import wraps
from datetime import datetime, timedelta
import re
from auth_client import AuthClient
from cnpj_handler import CNPJHandler
from transaction_handler import TransactionHandler

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

# Global variables
upload_progress = {}  # Dictionary to track file upload progress
failed_cnpjs = set()  # Set to track failed CNPJ lookups

# Initialize handlers
auth_client = AuthClient(
    auth_server_url=os.getenv('AUTH_SERVER_URL', 'https://af360bank.onrender.com'),
    app_name=os.getenv('APP_NAME', 'financeiro')
)
cnpj_handler = CNPJHandler()
transaction_handler = TransactionHandler()

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

def find_header_row(df):
    header_keywords = ['data', 'histórico', 'valor', 'date', 'historic', 'value']
    
    for idx, row in df.iterrows():
        row_values = [str(val).lower().strip() for val in row if not pd.isna(val)]
        if any(keyword in value for value in row_values for keyword in header_keywords):
            return idx
    return 0

def find_matching_column(df, possible_names):
    for name in possible_names:
        matches = [col for col in df.columns if str(name).lower() in str(col).lower()]
        if matches:
            return matches[0]
    return None

def extract_and_enrich_cnpj(description, transaction_type):
    cnpj_patterns = [
        r'CNPJ[:\s]*(\d{14,15})',
        r'CNPJ[:\s]*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})',
        r'(\d{14})(?=\D|$)'
    ]
    
    for pattern in cnpj_patterns:
        match = re.search(pattern, description)
        if match:
            cnpj = match.group(1)
            cnpj = re.sub(r'\D', '', cnpj)
            
            if len(cnpj) >= 14:
                cnpj = cnpj[:14]
                
                if transaction_type in ['PIX RECEBIDO', 'TED RECEBIDA', 'PAGAMENTO']:
                    company_info = cnpj_handler.get_company_info(cnpj)
                    if company_info and 'razao_social' in company_info:
                        return description.replace(
                            match.group(0),
                            f"CNPJ {cnpj} - {company_info['razao_social']}"
                        )
                    else:
                        failed_cnpjs.add(cnpj)
    
    return description

def extract_transaction_info(historico, valor):
    historico = historico.upper()
    info = {
        'tipo': None,
        'valor': valor,
        'identificador': None,
        'document': None,
        'description': historico
    }
    
    tipo_mapping = {
        'PIX RECEBIDO': ['PIX RECEBIDO'],
        'PIX ENVIADO': ['PIX ENVIADO'],
        'TED RECEBIDA': ['TED RECEBIDA', 'TED CREDIT'],
        'TED ENVIADA': ['TED ENVIADA', 'TED DEBIT'],
        'PAGAMENTO': ['PAGAMENTO', 'PGTO', 'PAG'],
        'TARIFA': ['TARIFA', 'TAR'],
        'IOF': ['IOF'],
        'RESGATE': ['RESGATE'],
        'APLICACAO': ['APLICACAO', 'APLICAÇÃO'],
        'COMPRA': ['COMPRA'],
        'COMPENSACAO': ['COMPENSACAO', 'COMPENSAÇÃO'],
        'CHEQUE DEVOLVIDO': ['CHEQUE DEVOLVIDO', 'CH DEVOLVIDO'],
        'JUROS': ['JUROS'],
        'MULTA': ['MULTA'],
        'ANTECIPACAO': ['ANTECIPACAO', 'ANTECIPAÇÃO'],
        'CHEQUE EMITIDO': ['CHEQUE EMITIDO', 'CH EMITIDO']
    }
    
    for tipo, keywords in tipo_mapping.items():
        if any(keyword in historico for keyword in keywords):
            info['tipo'] = tipo
            break
    
    if info['tipo'] is None:
        info['tipo'] = 'OUTROS'
    
    # Enrich description with CNPJ info if available
    info['description'] = extract_and_enrich_cnpj(historico, info['tipo'])
    
    return info

def process_file_with_progress(filepath, process_id):
    try:
        # Initialize database first
        init_db()
        
        # Initialize progress
        upload_progress[process_id].update({
            'status': 'processing',
            'message': 'Reading file...'
        })

        # Read Excel file
        df = pd.read_excel(filepath)
        
        # Find header row
        header_row = find_header_row(df)
        if header_row > 0:
            new_columns = [str(val).strip() if not pd.isna(val) else f'Column_{i}' 
                         for i, val in enumerate(df.iloc[header_row])]
            df.columns = new_columns
            df = df.iloc[header_row + 1:].reset_index(drop=True)

        upload_progress[process_id].update({
            'total': len(df),
            'current': 0,
            'message': 'Processing transactions...'
        })

        # Find columns
        data_col = find_matching_column(df, ['Data', 'DATE', 'DT'])
        desc_col = find_matching_column(df, ['Histórico', 'HISTORIC', 'DESCRIÇÃO', 'DESCRICAO'])
        valor_col = find_matching_column(df, ['Valor', 'VALUE', 'QUANTIA'])

        if not all([data_col, desc_col, valor_col]):
            raise Exception("Required columns not found")

        conn = get_db_connection()
        cursor = conn.cursor()

        for index, row in df.iterrows():
            try:
                # Update progress
                upload_progress[process_id]['current'] = index + 1
                upload_progress[process_id]['message'] = f'Processing row {index + 1} of {len(df)}'

                # Skip empty rows
                if pd.isna(row[data_col]) or pd.isna(row[desc_col]) or pd.isna(row[valor_col]):
                    continue

                # Process date
                try:
                    if isinstance(row[data_col], str):
                        try:
                            date = datetime.strptime(row[data_col], '%d/%m/%Y').date()
                        except ValueError:
                            date = pd.to_datetime(row[data_col]).date()
                    else:
                        date = pd.to_datetime(row[data_col]).date()
                except Exception as e:
                    print(f"Error processing date at row {index}: {row[data_col]}")
                    continue

                # Process description and value
                description = str(row[desc_col]).strip()
                valor = row[valor_col]
                
                if isinstance(valor, (int, float)):
                    value = float(valor)
                else:
                    valor_str = str(valor).replace('R$', '').strip()
                    value = float(valor_str.replace('.', '').replace(',', '.'))

                # Extract transaction info
                info = extract_transaction_info(description, value)

                # Insert into database
                cursor.execute('''
                    INSERT INTO transactions (date, description, value, type, transaction_type, document)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    date,
                    info['description'],
                    value,
                    'CREDITO' if value > 0 else 'DEBITO',
                    info['tipo'],
                    info.get('document', '')
                ))

            except Exception as e:
                print(f"Error processing row {index}: {str(e)}")
                continue

        conn.commit()
        conn.close()

        upload_progress[process_id].update({
            'status': 'completed',
            'message': 'Processing completed successfully'
        })

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
        ensure_upload_folder()
        
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
        if progress['status'] in ['completed', 'error']:
            del upload_progress[process_id]
        return jsonify(progress)
    return jsonify({'status': 'not_found'})

@app.route('/recebidos')
@login_required
def recebidos():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get filter parameters
    tipo_filtro = request.args.get('tipo', 'todos')
    cnpj_filtro = request.args.get('cnpj', 'todos')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Base query
    query = '''
        SELECT * FROM transactions 
        WHERE type = 'CREDITO'
    '''
    params = []
    
    # Add tipo filter
    if tipo_filtro != 'todos':
        query += ' AND transaction_type = ?'
        params.append(tipo_filtro)
    
    # Add CNPJ filter
    if cnpj_filtro != 'todos':
        query += ' AND description LIKE ?'
        params.append(f'%{cnpj_filtro}%')
    
    # Add date filters
    if start_date:
        query += ' AND date >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND date <= ?'
        params.append(end_date)
    
    # Add ordering
    query += ' ORDER BY date DESC'
    
    # Get transactions
    cursor.execute(query, params)
    transactions = cursor.fetchall()
    
    # Calculate totals
    totals_query = '''
        SELECT 
            SUM(CASE WHEN transaction_type = 'PIX RECEBIDO' THEN value ELSE 0 END) as pix_recebido,
            SUM(CASE WHEN transaction_type = 'TED RECEBIDA' THEN value ELSE 0 END) as ted_recebida,
            SUM(CASE WHEN transaction_type = 'PAGAMENTO' THEN value ELSE 0 END) as pagamento,
            SUM(value) as total
        FROM transactions 
        WHERE type = 'CREDITO'
    '''
    
    # Apply the same filters to totals
    if tipo_filtro != 'todos' or cnpj_filtro != 'todos' or start_date or end_date:
        totals_query = totals_query.replace('WHERE type = ', 'WHERE type = ') + query[query.find('AND'):]
    
    cursor.execute(totals_query, params)
    totals_row = cursor.fetchone()
    
    # Get unique CNPJs for filter dropdown
    cursor.execute('''
        SELECT DISTINCT 
            substr(description, 
                instr(description, 'CNPJ ') + 5, 
                14) as cnpj,
            MAX(description) as name
        FROM transactions 
        WHERE type = 'CREDITO' 
        AND description LIKE '%CNPJ%'
        GROUP BY substr(description, 
            instr(description, 'CNPJ ') + 5, 
            14)
    ''')
    cnpjs = [{'cnpj': row['cnpj'], 'name': row['name']} for row in cursor.fetchall()]
    
    totals = {
        'pix_recebido': totals_row['pix_recebido'] or 0,
        'ted_recebida': totals_row['ted_recebida'] or 0,
        'pagamento': totals_row['pagamento'] or 0,
        'total': totals_row['total'] or 0
    }
    
    conn.close()
    
    return render_template('recebidos.html', 
                         transactions=transactions,
                         totals=totals,
                         cnpjs=cnpjs,
                         tipo_filtro=tipo_filtro,
                         cnpj_filtro=cnpj_filtro,
                         start_date=start_date,
                         end_date=end_date,
                         active_page='recebidos',
                         failed_cnpjs=len(failed_cnpjs))

@app.route('/retry_failed_cnpjs')
@login_required
def retry_failed_cnpjs():
    global failed_cnpjs
    success = True
    
    try:
        # Create a copy of failed_cnpjs to iterate over
        cnpjs_to_retry = failed_cnpjs.copy()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for cnpj in cnpjs_to_retry:
            company_info = cnpj_handler.get_company_info(cnpj)
            if company_info and 'razao_social' in company_info:
                # Update descriptions in database
                cursor.execute('''
                    UPDATE transactions 
                    SET description = REPLACE(
                        description,
                        'CNPJ ' || ?,
                        'CNPJ ' || ? || ' - ' || ?
                    )
                    WHERE description LIKE ?
                ''', (cnpj, cnpj, company_info['razao_social'], f'%CNPJ {cnpj}%'))
                
                failed_cnpjs.remove(cnpj)
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error retrying failed CNPJs: {str(e)}")
        success = False
    
    return jsonify({'success': success})
        
    except Exception as e:
        print(f"Error retrying failed CNPJs: {str(e)}")
        success = False
    
    return jsonify({'success': success})

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
    
    return render_template('enviados.html', 
                         transactions=transactions, 
                         active_page='enviados',
                         failed_cnpjs=len(failed_cnpjs))

@app.route('/transactions')
@login_required
def transactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM transactions ORDER BY date DESC')
    transactions = cursor.fetchall()
    conn.close()
    
    return render_template('transactions.html', 
                         transactions=transactions, 
                         active_page='transactions',
                         failed_cnpjs=len(failed_cnpjs))

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
    
    return render_template('transactions_summary.html', 
                         transactions_summary=summary, 
                         active_page='transactions_summary',
                         failed_cnpjs=len(failed_cnpjs))

@app.route('/cnpj_verification', methods=['GET', 'POST'])
@login_required
def cnpj_verification():
    if request.method == 'POST':
        cnpj = request.form.get('cnpj')
        if cnpj:
            company_info = cnpj_handler.get_company_info(cnpj)
            if company_info:
                return render_template('cnpj_verification.html',
                                    active_page='cnpj_verification',
                                    company_info=company_info,
                                    failed_cnpjs=len(failed_cnpjs))
            else:
                flash('CNPJ não encontrado ou serviço indisponível', 'error')
    
    return render_template('cnpj_verification.html',
                         active_page='cnpj_verification',
                         failed_cnpjs=len(failed_cnpjs))

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)