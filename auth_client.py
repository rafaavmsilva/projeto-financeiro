import requests
from functools import wraps
from flask import request, redirect, session, url_for

class AuthClient:
    def __init__(self, auth_server_url, app_name):
        self.auth_server_url = auth_server_url
        self.app_name = app_name

    def verify_token(self, token):
        response = requests.post(
            f"{self.auth_server_url}/api/verify_token",
            json={
                'token': token,
                'app_name': self.app_name
            }
        )
        return response.json() if response.ok else None

    def login_required(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        return decorated_function

# Example usage in other applications:
"""
from flask import Flask, request, session, redirect, url_for
from auth_client import AuthClient

app = Flask(__name__)
auth = AuthClient('http://localhost:5000', 'projeto-financeiro')  # or 'sistema-comissoes'

@app.route('/auth/callback')
def auth_callback():
    token = request.args.get('token')
    if not token:
        return 'No token provided', 400
        
    result = auth.verify_token(token)
    if result and result.get('valid'):
        session['user'] = result['user']
        return redirect(url_for('index'))
    return 'Invalid token', 400

@app.route('/')
@auth.login_required
def index():
    return f"Welcome {session['user']['name']} to {auth.app_name}!"
"""
