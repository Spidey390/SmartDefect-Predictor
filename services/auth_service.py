"""
Authentication Service for SmartDefect Predictor
Handles user authentication, authorization, and role-based access control
"""
import sqlite3
import hashlib
import secrets
import os
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from datetime import datetime
from typing import Optional, Dict, Any, Tuple


DB_PATH = os.environ.get('DB_PATH', 'database.db')


def get_db_connection() -> sqlite3.Connection:
    """Get database connection with row factory"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db():
    """Initialize authentication tables"""
    conn = get_db_connection()
    c = conn.cursor()

    # Users table with role-based access
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            last_login TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')

    # Sessions table for tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            ip_address TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # Audit log for security tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            resource TEXT,
            ip_address TEXT,
            timestamp TEXT NOT NULL,
            details TEXT
        )
    ''')

    # Create default admin user if not exists
    c.execute('SELECT COUNT(*) FROM users WHERE role = "admin"')
    if c.fetchone()[0] == 0:
        # Default admin credentials - MUST be changed in production
        admin_password = hash_password('admin123')
        c.execute('''
            INSERT INTO users (username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', ('admin', 'admin@smartdefect.local', admin_password, 'admin', datetime.now().isoformat()))

    # Create default regular user if not exists
    c.execute('SELECT COUNT(*) FROM users WHERE role = "user"')
    if c.fetchone()[0] == 0:
        user_password = hash_password('user123')
        c.execute('''
            INSERT INTO users (username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', ('user', 'user@smartdefect.local', user_password, 'user', datetime.now().isoformat()))

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    """Hash password with salt using SHA-256"""
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{password_hash}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored hash"""
    try:
        salt, stored_hash = password_hash.split(':')
        check_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        return check_hash == stored_hash
    except Exception:
        return False


def generate_session_token() -> str:
    """Generate secure session token"""
    return secrets.token_urlsafe(32)


def create_user_session(user_id: int, ip_address: str = None) -> str:
    """Create new user session"""
    conn = get_db_connection()
    c = conn.cursor()

    session_token = generate_session_token()
    expires_at = datetime.now().isoformat()

    # Session expires in 24 hours
    from datetime import timedelta
    expires_at = (datetime.now() + timedelta(hours=24)).isoformat()

    c.execute('''
        INSERT INTO user_sessions (user_id, session_token, created_at, expires_at, ip_address)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, session_token, datetime.now().isoformat(), expires_at, ip_address))

    # Update last login
    c.execute('''
        UPDATE users SET last_login = ? WHERE id = ?
    ''', (datetime.now().isoformat(), user_id))

    conn.commit()
    conn.close()

    return session_token


def validate_session(session_token: str) -> Optional[Dict[str, Any]]:
    """Validate session token and return user info"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute('''
        SELECT u.id, u.username, u.email, u.role, u.is_active,
               s.expires_at, s.ip_address
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_token = ? AND s.expires_at > ?
    ''', (session_token, datetime.now().isoformat()))

    row = c.fetchone()
    conn.close()

    if row:
        return {
            'id': row['id'],
            'username': row['username'],
            'email': row['email'],
            'role': row['role'],
            'is_active': row['is_active'],
            'ip_address': row['ip_address']
        }
    return None


def invalidate_session(session_token: str):
    """Invalidate session token"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM user_sessions WHERE session_token = ?', (session_token,))
    conn.commit()
    conn.close()


def cleanup_expired_sessions():
    """Remove expired sessions from database"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM user_sessions WHERE expires_at <= ?', (datetime.now().isoformat(),))
    conn.commit()
    conn.close()


def log_audit_action(user_id: Optional[int], username: str, action: str,
                     resource: str = None, ip_address: str = None, details: str = None):
    """Log security audit action"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO audit_log (user_id, username, action, resource, ip_address, timestamp, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, action, resource, ip_address, datetime.now().isoformat(), details))
    conn.commit()
    conn.close()


def register_user(username: str, email: str, password: str, role: str = 'user') -> Tuple[bool, str]:
    """
    Register new user

    Returns:
        Tuple of (success, message)
    """
    # Validate inputs
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"

    if not email or '@' not in email:
        return False, "Invalid email address"

    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters"

    if role not in ['user', 'admin']:
        role = 'user'  # Default to user role

    password_hash = hash_password(password)

    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            INSERT INTO users (username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (username, email, password_hash, role, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        log_audit_action(None, username, 'USER_REGISTERED', ip_address=request.remote_addr)
        return True, "User registered successfully"

    except sqlite3.IntegrityError as e:
        if 'username' in str(e):
            return False, "Username already exists"
        elif 'email' in str(e):
            return False, "Email already registered"
        return False, "Registration failed"


def authenticate_user(username: str, password: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Authenticate user with username and password

    Returns:
        Tuple of (user_info or None, message)
    """
    conn = get_db_connection()
    c = conn.cursor()

    c.execute('''
        SELECT id, username, email, password_hash, role, is_active
        FROM users
        WHERE username = ? OR email = ?
    ''', (username, username))

    row = c.fetchone()
    conn.close()

    if not row:
        return None, "Invalid username or password"

    if not row['is_active']:
        return None, "Account is deactivated"

    if not verify_password(password, row['password_hash']):
        return None, "Invalid username or password"

    user_info = {
        'id': row['id'],
        'username': row['username'],
        'email': row['email'],
        'role': row['role']
    }

    log_audit_action(
        row['id'], row['username'], 'USER_LOGIN',
        ip_address=request.remote_addr,
        details=f"Login successful - Role: {row['role']}"
    )

    return user_info, "Login successful"


# Flask decorators for authentication
def login_required(f):
    """Decorator to require login for route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role for route"""
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
    """Get current logged-in user from session"""
    if 'user_id' not in session:
        return None

    return {
        'id': session.get('user_id'),
        'username': session.get('username'),
        'email': session.get('email'),
        'role': session.get('user_role')
    }


def create_user_from_google(google_user_info: dict) -> Tuple[Dict[str, Any], bool]:
    """
    Create or get user from Google OAuth info

    Returns:
        Tuple of (user_info, is_new_user)
    """
    conn = get_db_connection()
    c = conn.cursor()

    google_email = google_user_info.get('email')

    # Check if user exists
    c.execute('SELECT id, username, email, role, is_active FROM users WHERE email = ?', (google_email,))
    row = c.fetchone()

    if row:
        # User exists, return existing user
        conn.close()
        return {
            'id': row['id'],
            'username': row['username'],
            'email': row['email'],
            'role': row['role']
        }, False

    # Create new user from Google info
    username = google_user_info.get('given_name', google_email.split('@')[0])
    # Ensure username is unique
    base_username = username
    counter = 1
    while True:
        c.execute('SELECT COUNT(*) FROM users WHERE username = ?', (username,))
        if c.fetchone()[0] == 0:
            break
        username = f"{base_username}{counter}"
        counter += 1

    c.execute('''
        INSERT INTO users (username, email, password_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (username, google_email, 'google_oauth', 'user', datetime.now().isoformat()))

    conn.commit()
    conn.close()

    log_audit_action(None, username, 'USER_REGISTERED_GOOGLE', ip_address=request.remote_addr)

    return {
        'id': c.lastrowid,
        'username': username,
        'email': google_email,
        'role': 'user'
    }, True
