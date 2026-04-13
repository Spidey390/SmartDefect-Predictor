"""
SmartDefect Predictor - Production Flask Application
Defect prediction system with JIRA integration and role-based access control
"""
from flask import Flask, request, jsonify, send_file, render_template, session, redirect, url_for
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import sqlite3
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from functools import wraps
import logging
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from services.jira_service import JiraService, extract_issue_data
from services.data_processor import (
    group_by_component,
    create_ml_dataset,
    prepare_features_for_ml,
    add_risk_predictions,
    generate_summary_statistics
)
from services.auth_service import (
    init_auth_db,
    register_user,
    authenticate_user,
    create_user_session,
    validate_session,
    invalidate_session,
    log_audit_action,
    get_db_connection,
    login_required,
    admin_required,
    get_current_user,
    cleanup_expired_sessions,
    create_user_from_google
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# Configure Google OAuth
from authlib.integrations.flask_client import OAuth

oauth = OAuth(app)
google_client_id = os.environ.get('GOOGLE_CLIENT_ID')
google_client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')

if google_client_id and google_client_secret:
    oauth.register(
        name='google',
        client_id=google_client_id,
        client_secret=google_client_secret,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile'
        }
    )

# Initialize CORS for API endpoints
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
DB_PATH = os.environ.get('DB_PATH', 'database.db')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def init_database():
    """Initialize application database tables"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        timestamp TEXT,
        total_modules INTEGER,
        high_risk INTEGER,
        medium_risk INTEGER,
        low_risk INTEGER,
        success_rate REAL,
        user_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

    # Initialize authentication tables
    init_auth_db()

    # Cleanup expired sessions
    cleanup_expired_sessions()


# Security headers middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if os.environ.get('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# ==================== AUTHENTICATION ROUTES ====================

@app.route('/login', methods=['GET'])
def login():
    """Render login page"""
    if 'user_id' in session:
        if session.get('user_role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))
    return render_template('login.html')


@app.route('/register', methods=['GET'])
def register():
    """Render registration page"""
    return render_template('register.html')


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def api_login():
    """Authenticate user and create session"""
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400

    user_info, message = authenticate_user(username, password)

    if not user_info:
        return jsonify({'error': message}), 401

    # Create session
    session_token = create_user_session(user_info['id'], request.remote_addr)

    # Set Flask session
    session['user_id'] = user_info['id']
    session['username'] = user_info['username']
    session['email'] = user_info['email']
    session['user_role'] = user_info['role']
    session.permanent = True

    return jsonify({
        'success': True,
        'message': message,
        'role': user_info['role'],
        'username': user_info['username']
    })


@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("5 per minute")
def api_register():
    """Register new user"""
    data = request.get_json()
    username = data.get('username', '')
    email = data.get('email', '')
    password = data.get('password', '')

    success, message = register_user(username, email, password)

    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


@app.route('/api/auth/logout', methods=['GET', 'POST'])
def api_logout():
    """Logout current user"""
    if 'user_id' in session:
        user_id = session['user_id']
        username = session.get('username', 'unknown')
        log_audit_action(user_id, username, 'USER_LOGOUT', ip_address=request.remote_addr)

        # Invalidate session token if exists
        if 'session_token' in session:
            invalidate_session(session['session_token'])

    session.clear()
    return redirect(url_for('login'))


@app.route('/api/auth/me', methods=['GET'])
def get_current_user_api():
    """Get current logged-in user info"""
    user = get_current_user()
    if user:
        return jsonify({'success': True, 'user': user})
    return jsonify({'success': False, 'user': None})


@app.route('/api/auth/google')
def google_login():
    """Initiate Google OAuth login"""
    if not google_client_id:
        return redirect(url_for('login', error='Google OAuth not configured. Please check your .env file.'))

    redirect_uri = url_for('google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/api/auth/google/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
        token = oauth.google.authorize_access_token()
        google_user = token.get('userinfo')

        if not google_user:
            return redirect(url_for('login'))

        # Create or get user from Google info
        user_info, is_new = create_user_from_google(google_user)

        # Create session
        session_token = create_user_session(user_info['id'], request.remote_addr)

        # Set Flask session
        session['user_id'] = user_info['id']
        session['username'] = user_info['username']
        session['email'] = user_info['email']
        session['user_role'] = user_info['role']
        session['google_auth'] = True
        session.permanent = True

        log_audit_action(
            user_info['id'],
            user_info['username'],
            'USER_LOGIN_GOOGLE' if not is_new else 'USER_REGISTERED_GOOGLE',
            ip_address=request.remote_addr
        )

        return redirect(url_for('user_dashboard'))

    except Exception as e:
        logger.error(f"Google OAuth error: {str(e)}")
        return redirect(url_for('login'))


@app.route('/unauthorized')
def unauthorized():
    """Render unauthorized access page"""
    return render_template('error.html', error="Access Denied", message="You don't have permission to access this page."), 403


# ==================== DASHBOARD ROUTES ====================

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Render admin dashboard"""
    return render_template('admin_dashboard.html')


@app.route('/dashboard')
@login_required
def user_dashboard():
    """Render user dashboard"""
    return render_template('user_dashboard.html')


@app.route('/profile')
@login_required
def user_profile():
    """Render user profile page"""
    return render_template('profile.html')


@app.route('/admin/users')
@admin_required
def admin_users():
    """Render admin users management page"""
    return render_template('admin_users.html')


@app.route('/admin/audit')
@admin_required
def admin_audit():
    """Render admin audit log page"""
    return render_template('admin_audit.html')


@app.route('/admin/settings')
@admin_required
def admin_settings():
    """Render admin settings page"""
    return render_template('admin_settings.html')


# ==================== ADMIN API ROUTES ====================

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def get_admin_stats():
    """Get admin dashboard statistics"""
    conn = get_db_connection()
    c = conn.cursor()

    # Get user counts
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]

    c.execute('SELECT COUNT(*) FROM users WHERE role = "user"')
    regular_users = c.fetchone()[0]

    c.execute('SELECT COUNT(*) FROM users WHERE role = "admin"')
    admin_users = c.fetchone()[0]

    # Get analysis count
    c.execute('SELECT COUNT(*) FROM analyses')
    total_analyses = c.fetchone()[0]

    conn.close()

    return jsonify({
        'success': True,
        'stats': {
            'total_users': total_users,
            'regular_users': regular_users,
            'admin_users': admin_users,
            'total_analyses': total_analyses
        }
    })


@app.route('/api/admin/recent-activity', methods=['GET'])
@admin_required
def get_recent_activity():
    """Get recent audit log activity"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute('''
        SELECT username, action, resource, timestamp, details
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT 10
    ''')

    rows = c.fetchall()
    conn.close()

    activities = [{
        'username': row['username'],
        'action': row['action'],
        'resource': row['resource'],
        'timestamp': row['timestamp'],
        'details': row['details']
    } for row in rows]

    return jsonify({'success': True, 'activities': activities})


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def get_all_users():
    """Get all users (admin only)"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute('''
        SELECT id, username, email, role, created_at, last_login, is_active
        FROM users
        ORDER BY created_at DESC
    ''')

    rows = c.fetchall()
    conn.close()

    users = [{
        'id': row['id'],
        'username': row['username'],
        'email': row['email'],
        'role': row['role'],
        'created_at': row['created_at'],
        'last_login': row['last_login'],
        'is_active': bool(row['is_active'])
    } for row in rows]

    return jsonify({'success': True, 'users': users})


# ==================== MAIN APPLICATION ROUTES ====================

@app.route('/')
def index():
    """Redirect to login or dashboard"""
    if 'user_id' in session:
        if session.get('user_role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))


@app.route('/upload', methods=['POST'])
@login_required
def upload():
    """Upload and analyze CSV file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    try:
        # Try reading CSV with flexible parsing to handle inconsistent fields
        try:
            df = pd.read_csv(filepath, on_bad_lines='skip', engine='python')
        except TypeError:
            # For older pandas versions that don't support on_bad_lines
            df = pd.read_csv(filepath, error_bad_lines=False, warn_bad_lines=True, engine='python')

        # If CSV has inconsistent columns, try alternative parsing
        if len(df.columns) == 1:
            # Try reading with different delimiters
            for sep in [',', ';', '\t', '|']:
                try:
                    df = pd.read_csv(filepath, sep=sep, on_bad_lines='skip', engine='python')
                    if len(df.columns) > 1:
                        break
                except:
                    continue

        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        defect_col = None
        for col in df.columns:
            if 'defect' in col.lower():
                defect_col = col
                break
        feature_cols = [c for c in numeric_cols if c != defect_col]

        if len(feature_cols) < 1:
            return jsonify({'error': 'No numeric feature columns found in CSV'}), 400

        df_clean = df[feature_cols].dropna().copy()
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(df_clean)
        kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
        clusters = kmeans.fit_predict(X_scaled)
        df_clean['cluster'] = clusters
        cluster_means = df_clean.groupby('cluster')[feature_cols[0]].mean()
        sorted_clusters = cluster_means.sort_values(ascending=False).index.tolist()
        risk_map = {sorted_clusters[0]: 'HIGH', sorted_clusters[1]: 'MEDIUM', sorted_clusters[2]: 'LOW'}
        risk_score_map = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
        df_clean['risk_level'] = df_clean['cluster'].map(risk_map)
        df_clean['risk_score'] = df_clean['risk_level'].map(risk_score_map)
        df_clean['timestamp'] = datetime.now().isoformat()
        df_clean['module_id'] = range(1, len(df_clean) + 1)
        df_clean['analysis_file'] = file.filename

        total = len(df_clean)
        high = int((df_clean['risk_level'] == 'HIGH').sum())
        medium = int((df_clean['risk_level'] == 'MEDIUM').sum())
        low = int((df_clean['risk_level'] == 'LOW').sum())
        success_rate = round((low / total) * 100, 2) if total > 0 else 0

        output_csv = os.path.join(OUTPUT_FOLDER, 'defects_with_risk.csv')
        df_clean.to_csv(output_csv, index=False)

        powerbi_df = df_clean.copy()
        powerbi_df['month'] = datetime.now().strftime('%B %Y')
        powerbi_df['project'] = file.filename.replace('.csv', '')
        powerbi_csv = os.path.join(OUTPUT_FOLDER, 'powerbi_export.csv')
        powerbi_df.to_csv(powerbi_csv, index=False)

        # Save to database with user_id
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO analyses (filename, timestamp, total_modules, high_risk, medium_risk, low_risk, success_rate, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (file.filename, datetime.now().isoformat(), total, high, medium, low, success_rate, session.get('user_id')))
        conn.commit()
        conn.close()

        modules = df_clean[['module_id'] + feature_cols + ['risk_level', 'risk_score']].head(100).to_dict(orient='records')

        # Log audit action
        log_audit_action(
            session.get('user_id'),
            session.get('username'),
            'FILE_ANALYZED',
            resource=file.filename,
            ip_address=request.remote_addr,
            details=f"Analyzed {total} modules"
        )

        return jsonify({
            'success': True,
            'stats': {'total': total, 'high': high, 'medium': medium, 'low': low, 'success_rate': success_rate},
            'modules': modules,
            'features': feature_cols
        })
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/history')
@login_required
def history():
    """Render history page"""
    return render_template('history.html')


@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    """Get analysis history for current user (admin sees all)"""
    conn = get_db_connection()
    c = conn.cursor()

    if session.get('user_role') == 'admin':
        c.execute('SELECT * FROM analyses ORDER BY id DESC LIMIT 50')
    else:
        c.execute('SELECT * FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT 50', (session.get('user_id'),))

    rows = c.fetchall()
    conn.close()

    keys = ['id', 'filename', 'timestamp', 'total_modules', 'high_risk', 'medium_risk', 'low_risk', 'success_rate']
    return jsonify([dict(zip(keys, row)) for row in rows])


@app.route('/download/csv')
@login_required
def download_csv():
    """Download results CSV"""
    path = os.path.join(OUTPUT_FOLDER, 'defects_with_risk.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'No results yet. Upload a file first.'}), 404
    return send_file(path, as_attachment=True)


@app.route('/download/powerbi')
@login_required
def download_powerbi():
    """Download Power BI export CSV"""
    path = os.path.join(OUTPUT_FOLDER, 'powerbi_export.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'No results yet. Upload a file first.'}), 404
    return send_file(path, as_attachment=True)


# ==================== JIRA API ENDPOINTS ====================

@app.route('/api/jira/test', methods=['POST'])
@login_required
def test_jira_connection():
    """Test connection to JIRA API with provided credentials"""
    data = request.get_json()

    jira_url = data.get('jira_url', os.getenv('JIRA_URL'))
    email = data.get('email', os.getenv('JIRA_EMAIL'))
    api_token = data.get('api_token', os.getenv('JIRA_API_TOKEN'))

    if not all([jira_url, email, api_token]):
        return jsonify({'error': 'Missing JIRA credentials'}), 400

    jira = JiraService(jira_url, email, api_token)
    result = jira.test_connection()

    return jsonify(result)


@app.route('/api/jira/analyze', methods=['POST'])
@login_required
@limiter.limit("5 per hour")
def analyze_jira_project():
    """
    Analyze a JIRA project for defect prediction
    """
    data = request.get_json()

    jira_url = data.get('jira_url', os.getenv('JIRA_URL'))
    email = data.get('email', os.getenv('JIRA_EMAIL'))
    api_token = data.get('api_token', os.getenv('JIRA_API_TOKEN'))
    project_key = data.get('project_key')
    include_bugs_only = data.get('include_bugs_only', False)
    max_issues = data.get('max_issues', 1000)

    if not all([jira_url, email, api_token, project_key]):
        return jsonify({'error': 'Missing required JIRA parameters'}), 400

    try:
        # Step 1: Initialize JIRA service
        jira = JiraService(jira_url, email, api_token)

        # Step 2: Fetch issues from JIRA
        if include_bugs_only:
            result = jira.get_bugs_only(project_key, max_issues)
        else:
            result = jira.get_project_issues(project_key, max_issues)

        if not result['success']:
            return jsonify(result), 400

        # Step 3: Extract issue data
        issues = extract_issue_data(result['issues'])

        # Step 4: Group by component/label and calculate metrics
        component_metrics = group_by_component(issues)

        # If no components found, treat each issue as its own module
        if len(component_metrics) <= 1 and '_uncategorized_' in component_metrics:
            from collections import defaultdict
            type_metrics = defaultdict(lambda: {
                'total_issues': 0, 'bugs': 0, 'high_priority_bugs': 0,
                'open_issues': 0, 'closed_issues': 0, 'priority_sum': 0.0, 'issues': []
            })

            for issue in issues:
                type_key = issue['issue_type'] or 'Unknown'
                metrics = type_metrics[type_key]
                metrics['total_issues'] += 1
                metrics['issues'].append(issue['key'])
                if issue['is_bug']:
                    metrics['bugs'] += 1
                    if issue['priority_weight'] >= 3.5:
                        metrics['high_priority_bugs'] += 1
                if issue['is_open']:
                    metrics['open_issues'] += 1
                else:
                    metrics['closed_issues'] += 1
                metrics['priority_sum'] += issue['priority_weight']

            component_metrics = {}
            for type_name, metrics in type_metrics.items():
                total = metrics['total_issues']
                component_metrics[type_name] = {
                    'total_issues': total,
                    'bug_count': metrics['bugs'],
                    'high_priority_bugs': metrics['high_priority_bugs'],
                    'open_issues': metrics['open_issues'],
                    'closed_issues': metrics['closed_issues'],
                    'avg_priority': metrics['priority_sum'] / total if total > 0 else 0,
                    'bug_rate': metrics['bugs'] / total if total > 0 else 0,
                    'open_rate': metrics['open_issues'] / total if total > 0 else 0,
                    'issue_keys': metrics['issues']
                }

        # Step 5: Create ML dataset
        ml_df = create_ml_dataset(component_metrics, add_synthetic_metrics=True)

        # Step 6: Prepare features for ML
        feature_cols, module_names, X = prepare_features_for_ml(ml_df)

        if len(X) < 3:
            return jsonify({'error': 'Not enough data points for clustering. Need at least 3 modules.'}), 400

        # Step 7: Run KMeans clustering
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        n_clusters = min(3, len(X))
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        clusters = kmeans.fit_predict(X_scaled)

        # Step 8: Map clusters to risk levels
        cluster_means = pd.DataFrame(X_scaled).groupby(clusters)[0].mean()
        sorted_clusters = cluster_means.sort_values(ascending=False).index.tolist()

        risk_map = {}
        for i, cluster_id in enumerate(sorted_clusters):
            if i == 0:
                risk_map[cluster_id] = 'HIGH'
            elif i == 1:
                risk_map[cluster_id] = 'MEDIUM'
            else:
                risk_map[cluster_id] = 'LOW'

        risk_score_map = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

        # Step 9: Add predictions to dataset
        ml_df['cluster'] = clusters
        ml_df['risk_level'] = [risk_map[c] for c in clusters]
        ml_df['risk_score'] = [risk_score_map[risk_map[c]] for c in clusters]
        ml_df['timestamp'] = datetime.now().isoformat()
        ml_df['project_key'] = project_key
        ml_df['source'] = 'jira'

        # Step 10: Generate summary statistics
        stats = generate_summary_statistics(ml_df)

        # Step 11: Save results to database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO analyses
            (filename, timestamp, total_modules, high_risk, medium_risk, low_risk, success_rate, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (f"JIRA:{project_key}", datetime.now().isoformat(),
             stats['total_modules'], stats['high_risk'],
             stats['medium_risk'], stats['low_risk'], stats['success_rate'],
             session.get('user_id')))
        conn.commit()
        conn.close()

        # Step 12: Save to CSV
        output_csv = os.path.join(OUTPUT_FOLDER, f'jira_{project_key}_analysis.csv')
        ml_df.to_csv(output_csv, index=False)

        # Prepare response data
        modules = []
        for _, row in ml_df.head(100).iterrows():
            module = {
                'module_id': row.get('module_name', 'Unknown'),
                'total_issues': int(row.get('total_issues', 0)),
                'defect_count': int(row.get('defect_count', 0)),
                'high_priority_defects': int(row.get('high_priority_defects', 0)),
                'avg_priority': float(row.get('avg_priority', 0)),
                'bug_rate': float(row.get('bug_rate', 0)),
                'risk_level': row.get('risk_level', 'UNKNOWN'),
                'risk_score': int(row.get('risk_score', 0))
            }
            modules.append(module)

        # Log audit action
        log_audit_action(
            session.get('user_id'),
            session.get('username'),
            'JIRA_ANALYZED',
            resource=project_key,
            ip_address=request.remote_addr,
            details=f"Analyzed JIRA project {project_key}"
        )

        return jsonify({
            'success': True,
            'project_key': project_key,
            'total_issues_fetched': result['total'],
            'stats': stats,
            'modules': modules,
            'features': feature_cols,
            'jira_data': {
                'total_bugs': int(ml_df['defect_count'].sum()),
                'high_priority_bugs': int(ml_df['high_priority_defects'].sum()),
                'avg_bug_rate': round(ml_df['bug_rate'].mean(), 3)
            }
        })

    except Exception as e:
        logger.error(f"JIRA analysis error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/jira/projects', methods=['GET'])
@login_required
def get_jira_projects():
    """Get list of accessible JIRA projects"""
    jira_url = request.args.get('jira_url', os.getenv('JIRA_URL'))
    email = request.args.get('email', os.getenv('JIRA_EMAIL'))
    api_token = request.args.get('api_token', os.getenv('JIRA_API_TOKEN'))

    if not all([jira_url, email, api_token]):
        return jsonify({'error': 'Missing JIRA credentials'}), 400

    try:
        jira = JiraService(jira_url, email, api_token)
        result = jira.get_projects()

        if result['success']:
            projects = result['projects']
            return jsonify({
                'success': True,
                'projects': [
                    {'key': p.get('key'), 'name': p.get('name'), 'type': p.get('projectTypeKey')}
                    for p in projects
                ]
            })
        return jsonify({'error': f'Failed to fetch projects: {result.get("message", "Unknown error")}'}), 400

    except Exception as e:
        logger.error(f"Fetch projects error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', error="404 Not Found", message="The page you requested doesn't exist."), 404


@app.errorhandler(500)
def server_error(error):
    return render_template('error.html', error="500 Server Error", message="An unexpected error occurred."), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429


# ==================== APPLICATION STARTUP ====================

if __name__ == "__main__":
    # Initialize database
    init_database()

    # Get port from environment
    port = int(os.environ.get("PORT", 10000))

    # Run based on environment
    if os.environ.get('FLASK_ENV') == 'production':
        logger.info(f"Starting production server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        logger.info(f"Starting development server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=True)
