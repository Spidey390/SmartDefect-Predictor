"""
Authentication Service for SmartDefect Predictor
Handles user authentication, authorization, and role-based access control
Using MongoDB as the backend database
"""
import hashlib
import secrets
import os
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from pymongo import MongoClient
from bson import ObjectId
from cryptography.fernet import Fernet
import base64


# ─── MongoDB Connection ───────────────────────────────────────────────────────

MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/')
MONGODB_DB_NAME = os.environ.get('MONGODB_DB_NAME', 'smartdefect_db')
_client = None


def get_db():
    """Return the MongoDB database instance (singleton)."""
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI)
    return _client[MONGODB_DB_NAME]


# Keep this for backward compat with app.py imports that call get_db_connection
def get_db_connection():
    return get_db()


# ─── Encryption helpers ───────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    raw_key = os.environ.get('ENCRYPTION_KEY', '')
    if not raw_key:
        # generate and cache a key if missing (not persistent – set in .env!)
        raw_key = Fernet.generate_key().decode()
    # Ensure it's valid Fernet key (32 url-safe base64 bytes)
    try:
        key_bytes = base64.urlsafe_b64decode(raw_key + '==')
        if len(key_bytes) == 32:
            return Fernet(base64.urlsafe_b64encode(key_bytes))
    except Exception:
        pass
    return Fernet(Fernet.generate_key())


def encrypt_value(plain: str) -> str:
    """Encrypt a string value."""
    if not plain:
        return ''
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_value(token: str) -> str:
    """Decrypt an encrypted string value."""
    if not token:
        return ''
    try:
        f = _get_fernet()
        return f.decrypt(token.encode()).decode()
    except Exception:
        return token  # fallback if not encrypted


# ─── Password helpers ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password with salt using SHA-256."""
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{password_hash}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, stored_hash = password_hash.split(':')
        check_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        return check_hash == stored_hash
    except Exception:
        return False


# ─── Database Init ────────────────────────────────────────────────────────────

def init_auth_db():
    """Initialize authentication collections and default users."""
    db = get_db()

    # Create indexes
    db.users.create_index('username', unique=True)
    db.users.create_index('email', unique=True)
    db.user_sessions.create_index('session_token', unique=True)
    db.user_sessions.create_index('expires_at')

    # Create default admin if no admin exists
    if db.users.count_documents({'role': 'admin'}) == 0:
        db.users.insert_one({
            'username': 'admin',
            'email': 'admin@smartdefect.local',
            'password_hash': hash_password('admin123'),
            'role': 'admin',
            'created_at': datetime.now().isoformat(),
            'last_login': None,
            'is_active': True,
            'jira_config': {}
        })

    # Create default regular user if none exists
    if db.users.count_documents({'role': 'user'}) == 0:
        db.users.insert_one({
            'username': 'user',
            'email': 'user@smartdefect.local',
            'password_hash': hash_password('user123'),
            'role': 'user',
            'created_at': datetime.now().isoformat(),
            'last_login': None,
            'is_active': True,
            'jira_config': {}
        })


# ─── Sessions ─────────────────────────────────────────────────────────────────

def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def create_user_session(user_id: str, ip_address: str = None) -> str:
    """Create a new session document in MongoDB."""
    db = get_db()
    session_token = generate_session_token()
    expires_at = (datetime.now() + timedelta(hours=24)).isoformat()

    db.user_sessions.insert_one({
        'user_id': str(user_id),
        'session_token': session_token,
        'created_at': datetime.now().isoformat(),
        'expires_at': expires_at,
        'ip_address': ip_address
    })

    db.users.update_one(
        {'_id': ObjectId(str(user_id))},
        {'$set': {'last_login': datetime.now().isoformat()}}
    )

    return session_token


def validate_session(session_token: str) -> Optional[Dict[str, Any]]:
    """Validate session token and return user info."""
    db = get_db()
    ses = db.user_sessions.find_one({
        'session_token': session_token,
        'expires_at': {'$gt': datetime.now().isoformat()}
    })
    if not ses:
        return None

    user = db.users.find_one({'_id': ObjectId(ses['user_id'])})
    if not user:
        return None

    return {
        'id': str(user['_id']),
        'username': user['username'],
        'email': user['email'],
        'role': user['role'],
        'is_active': user.get('is_active', True)
    }


def invalidate_session(session_token: str):
    db = get_db()
    db.user_sessions.delete_one({'session_token': session_token})


def cleanup_expired_sessions():
    db = get_db()
    db.user_sessions.delete_many({'expires_at': {'$lte': datetime.now().isoformat()}})


# ─── Audit Log ────────────────────────────────────────────────────────────────

def log_audit_action(user_id, username: str, action: str,
                     resource: str = None, ip_address: str = None, details: str = None):
    db = get_db()
    db.audit_log.insert_one({
        'user_id': str(user_id) if user_id else None,
        'username': username,
        'action': action,
        'resource': resource,
        'ip_address': ip_address,
        'timestamp': datetime.now().isoformat(),
        'details': details
    })


# ─── Registration & Authentication ───────────────────────────────────────────

def register_user(username: str, email: str, password: str, role: str = 'user') -> Tuple[bool, str]:
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"
    if not email or '@' not in email:
        return False, "Invalid email address"
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters"
    if role not in ['user', 'admin']:
        role = 'user'

    db = get_db()
    if db.users.find_one({'username': username}):
        return False, "Username already exists"
    if db.users.find_one({'email': email}):
        return False, "Email already registered"

    db.users.insert_one({
        'username': username,
        'email': email,
        'password_hash': hash_password(password),
        'role': role,
        'created_at': datetime.now().isoformat(),
        'last_login': None,
        'is_active': True,
        'jira_config': {}
    })

    try:
        log_audit_action(None, username, 'USER_REGISTERED', ip_address=request.remote_addr)
    except Exception:
        pass

    return True, "User registered successfully"


def authenticate_user(username: str, password: str) -> Tuple[Optional[Dict[str, Any]], str]:
    db = get_db()
    user = db.users.find_one({'$or': [{'username': username}, {'email': username}]})

    if not user:
        return None, "Invalid username or password"
    if not user.get('is_active', True):
        return None, "Account is deactivated"
    if not verify_password(password, user['password_hash']):
        return None, "Invalid username or password"

    user_info = {
        'id': str(user['_id']),
        'username': user['username'],
        'email': user['email'],
        'role': user['role']
    }

    try:
        log_audit_action(
            str(user['_id']), user['username'], 'USER_LOGIN',
            ip_address=request.remote_addr,
            details=f"Login successful - Role: {user['role']}"
        )
    except Exception:
        pass

    return user_info, "Login successful"


# ─── Jira Config per user ────────────────────────────────────────────────────

def save_jira_config(user_id: str, jira_url: str, jira_email: str, jira_token: str):
    """Save (and encrypt) per-user Jira credentials."""
    db = get_db()
    db.users.update_one(
        {'_id': ObjectId(user_id)},
        {'$set': {
            'jira_config': {
                'jira_url': jira_url,
                'jira_email': jira_email,
                'jira_token_enc': encrypt_value(jira_token)
            }
        }}
    )


def load_jira_config(user_id: str) -> Dict[str, str]:
    """Load and decrypt per-user Jira credentials."""
    db = get_db()
    user = db.users.find_one({'_id': ObjectId(user_id)}, {'jira_config': 1})
    if not user or not user.get('jira_config'):
        return {}
    cfg = user['jira_config']
    return {
        'jira_url': cfg.get('jira_url', ''),
        'jira_email': cfg.get('jira_email', ''),
        'jira_token': decrypt_value(cfg.get('jira_token_enc', ''))
    }


# ─── Google OAuth ─────────────────────────────────────────────────────────────

def create_user_from_google(google_user_info: dict) -> Tuple[Dict[str, Any], bool]:
    db = get_db()
    google_email = google_user_info.get('email')

    existing = db.users.find_one({'email': google_email})
    if existing:
        return {
            'id': str(existing['_id']),
            'username': existing['username'],
            'email': existing['email'],
            'role': existing['role']
        }, False

    username = google_user_info.get('given_name', google_email.split('@')[0])
    base_username = username
    counter = 1
    while db.users.find_one({'username': username}):
        username = f"{base_username}{counter}"
        counter += 1

    result = db.users.insert_one({
        'username': username,
        'email': google_email,
        'password_hash': 'google_oauth',
        'role': 'user',
        'created_at': datetime.now().isoformat(),
        'last_login': None,
        'is_active': True,
        'jira_config': {}
    })

    try:
        log_audit_action(None, username, 'USER_REGISTERED_GOOGLE', ip_address=request.remote_addr)
    except Exception:
        pass

    return {
        'id': str(result.inserted_id),
        'username': username,
        'email': google_email,
        'role': 'user'
    }, True


# ─── Flask Decorators ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        if session.get('user_role') != 'admin':
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def get_current_user() -> Optional[Dict[str, Any]]:
    if 'user_id' not in session:
        return None
    return {
        'id': session.get('user_id'),
        'username': session.get('username'),
        'email': session.get('email'),
        'role': session.get('user_role')
    }
