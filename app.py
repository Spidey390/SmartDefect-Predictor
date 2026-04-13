"""
SmartDefect Predictor - Production Flask Application
Defect prediction system with JIRA integration, role-based access control
and MongoDB persistence.
"""
from flask import Flask, request, jsonify, send_file, render_template, session, redirect, url_for
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from bson import ObjectId

from services.jira_service import JiraService, extract_issue_data
from services.data_processor import (
    group_by_component,
    create_ml_dataset,
    prepare_features_for_ml,
    add_risk_predictions,
    generate_summary_statistics
)
from services.auth_service import (
    get_db,
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
    create_user_from_google,
    save_jira_config,
    load_jira_config
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
        client_kwargs={'scope': 'openid email profile'}
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
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def init_database():
    """Initialize MongoDB collections and default data."""
    init_auth_db()
    cleanup_expired_sessions()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _oid(val):
    """Safely convert a value to ObjectId."""
    if isinstance(val, ObjectId):
        return val
    try:
        return ObjectId(str(val))
    except Exception:
        return None


# Security headers middleware
@app.after_request
def add_security_headers(response):
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
    if 'user_id' in session:
        if session.get('user_role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))
    return render_template('login.html')


@app.route('/register', methods=['GET'])
def register():
    return render_template('register.html')


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def api_login():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400

        username = data.get('username', '')
        password = data.get('password', '')

        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400

        user_info, message = authenticate_user(username, password)

        if not user_info:
            return jsonify({'error': message}), 401

        session_token = create_user_session(user_info['id'], request.remote_addr)

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
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("5 per minute")
def api_register():
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
    if 'user_id' in session:
        user_id = session['user_id']
        username = session.get('username', 'unknown')
        log_audit_action(user_id, username, 'USER_LOGOUT', ip_address=request.remote_addr)
        if 'session_token' in session:
            invalidate_session(session['session_token'])
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/auth/me', methods=['GET'])
def get_current_user_api():
    user = get_current_user()
    if user:
        return jsonify({'success': True, 'user': user})
    return jsonify({'success': False, 'user': None})


@app.route('/favicon.ico')
def favicon():
    return send_file(os.path.join(app.static_folder, 'favicon.ico'), mimetype='image/x-icon')


@app.route('/api/auth/google')
def google_login():
    if not google_client_id:
        return redirect(url_for('login', error='Google OAuth not configured.'))
    redirect_uri = url_for('google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/api/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        google_user = token.get('userinfo')

        if not google_user:
            return redirect(url_for('login'))

        user_info, is_new = create_user_from_google(google_user)
        session_token = create_user_session(user_info['id'], request.remote_addr)

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
    return render_template('error.html', error="Access Denied",
                           message="You don't have permission to access this page."), 403


# ==================== DASHBOARD ROUTES ====================

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin_dashboard.html')


@app.route('/dashboard')
@login_required
def user_dashboard():
    return render_template('user_dashboard.html')


@app.route('/profile')
@login_required
def user_profile():
    return render_template('profile.html')


@app.route('/admin/users')
@admin_required
def admin_users():
    return render_template('admin_users.html')


@app.route('/admin/audit')
@admin_required
def admin_audit():
    return render_template('admin_audit.html')


@app.route('/admin/settings')
@admin_required
def admin_settings():
    return render_template('admin_settings.html')


# ==================== ADMIN API ROUTES ====================

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def get_admin_stats():
    db = get_db()
    total_users = db.users.count_documents({})
    regular_users = db.users.count_documents({'role': 'user'})
    admin_users_count = db.users.count_documents({'role': 'admin'})
    total_analyses = db.analyses.count_documents({})

    return jsonify({
        'success': True,
        'stats': {
            'total_users': total_users,
            'regular_users': regular_users,
            'admin_users': admin_users_count,
            'total_analyses': total_analyses
        }
    })


@app.route('/api/admin/recent-activity', methods=['GET'])
@admin_required
def get_recent_activity():
    db = get_db()
    rows = list(db.audit_log.find({}, {'_id': 0}).sort('timestamp', -1).limit(10))
    return jsonify({'success': True, 'activities': rows})


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def get_all_users():
    db = get_db()
    users = []
    for u in db.users.find({}, {'password_hash': 0, 'jira_config': 0}):
        users.append({
            'id': str(u['_id']),
            'username': u['username'],
            'email': u['email'],
            'role': u['role'],
            'created_at': u.get('created_at'),
            'last_login': u.get('last_login'),
            'is_active': u.get('is_active', True)
        })
    return jsonify({'success': True, 'users': users})


# ==================== MAIN APPLICATION ROUTES ====================

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('user_role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))


@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    try:
        try:
            df = pd.read_csv(filepath, on_bad_lines='skip', engine='python')
        except TypeError:
            df = pd.read_csv(filepath, error_bad_lines=False, engine='python')

        if len(df.columns) == 1:
            for sep in [',', ';', '\t', '|']:
                try:
                    df = pd.read_csv(filepath, sep=sep, on_bad_lines='skip', engine='python')
                    if len(df.columns) > 1:
                        break
                except Exception:
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
        df_clean['module_id'] = range(1, len(df_clean) + 1)

        total = len(df_clean)
        high = int((df_clean['risk_level'] == 'HIGH').sum())
        medium = int((df_clean['risk_level'] == 'MEDIUM').sum())
        low = int((df_clean['risk_level'] == 'LOW').sum())
        success_rate = round((low / total) * 100, 2) if total > 0 else 0

        output_csv = os.path.join(OUTPUT_FOLDER, 'defects_with_risk.csv')
        df_clean.to_csv(output_csv, index=False)

        modules = df_clean[['module_id'] + feature_cols + ['risk_level', 'risk_score']].head(100).to_dict(orient='records')

        # Save full analysis to MongoDB
        db = get_db()
        result = db.analyses.insert_one({
            'filename': file.filename,
            'source': 'csv',
            'timestamp': datetime.now().isoformat(),
            'total_modules': total,
            'high_risk': high,
            'medium_risk': medium,
            'low_risk': low,
            'success_rate': success_rate,
            'user_id': session.get('user_id'),
            'username': session.get('username'),
            'features': feature_cols,
            'modules': modules
        })

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
            'analysis_id': str(result.inserted_id),
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
    return render_template('history.html')


@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    db = get_db()
    query = {} if session.get('user_role') == 'admin' else {'user_id': session.get('user_id')}
    rows = list(db.analyses.find(query, {'modules': 0}).sort('timestamp', -1).limit(50))

    result = []
    for r in rows:
        result.append({
            'id': str(r['_id']),
            'filename': r.get('filename', ''),
            'source': r.get('source', 'csv'),
            'timestamp': r.get('timestamp', ''),
            'total_modules': r.get('total_modules', 0),
            'high_risk': r.get('high_risk', 0),
            'medium_risk': r.get('medium_risk', 0),
            'low_risk': r.get('low_risk', 0),
            'success_rate': r.get('success_rate', 0)
        })
    return jsonify(result)


@app.route('/api/history/<analysis_id>', methods=['GET'])
@login_required
def get_history_detail(analysis_id):
    """Return full analysis detail (including modules) for a specific run."""
    db = get_db()
    oid = _oid(analysis_id)
    if not oid:
        return jsonify({'error': 'Invalid ID'}), 400

    query = {'_id': oid}
    if session.get('user_role') != 'admin':
        query['user_id'] = session.get('user_id')

    record = db.analyses.find_one(query)
    if not record:
        return jsonify({'error': 'Analysis not found'}), 404

    return jsonify({
        'id': str(record['_id']),
        'filename': record.get('filename', ''),
        'source': record.get('source', 'csv'),
        'timestamp': record.get('timestamp', ''),
        'stats': {
            'total': record.get('total_modules', 0),
            'high': record.get('high_risk', 0),
            'medium': record.get('medium_risk', 0),
            'low': record.get('low_risk', 0),
            'success_rate': record.get('success_rate', 0)
        },
        'modules': record.get('modules', []),
        'features': record.get('features', [])
    })


@app.route('/download/csv')
@login_required
def download_csv():
    path = os.path.join(OUTPUT_FOLDER, 'defects_with_risk.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'No results yet. Upload a file first.'}), 404
    return send_file(path, as_attachment=True)


@app.route('/download/powerbi')
@login_required
def download_powerbi():
    path = os.path.join(OUTPUT_FOLDER, 'powerbi_export.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'No results yet. Upload a file first.'}), 404
    return send_file(path, as_attachment=True)


# ==================== USER JIRA CONFIG API ====================

@app.route('/api/user/jira-config', methods=['GET'])
@login_required
def get_user_jira_config():
    """Return the current user's saved Jira credentials."""
    cfg = load_jira_config(session.get('user_id'))
    return jsonify({'success': True, 'config': cfg})


@app.route('/api/user/jira-config', methods=['POST'])
@login_required
def save_user_jira_config():
    """Save Jira credentials for the current user."""
    data = request.get_json()
    jira_url = data.get('jira_url', '').strip()
    jira_email = data.get('email', '').strip()
    jira_token = data.get('api_token', '').strip()

    if not all([jira_url, jira_email, jira_token]):
        return jsonify({'error': 'All Jira credentials are required'}), 400

    save_jira_config(session.get('user_id'), jira_url, jira_email, jira_token)
    return jsonify({'success': True, 'message': 'JIRA credentials saved successfully'})


# ==================== JIRA API ENDPOINTS ====================

@app.route('/api/jira/test', methods=['POST'])
@login_required
def test_jira_connection():
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
        jira = JiraService(jira_url, email, api_token)

        if include_bugs_only:
            result = jira.get_bugs_only(project_key, max_issues)
        else:
            result = jira.get_project_issues(project_key, max_issues)

        if not result['success']:
            return jsonify(result), 400

        issues = extract_issue_data(result['issues'])
        component_metrics = group_by_component(issues)

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

        ml_df = create_ml_dataset(component_metrics, add_synthetic_metrics=True)
        feature_cols, module_names, X = prepare_features_for_ml(ml_df)

        if len(X) < 3:
            return jsonify({'error': 'Not enough data points for clustering. Need at least 3 modules.'}), 400

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        n_clusters = min(3, len(X))
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        clusters = kmeans.fit_predict(X_scaled)

        cluster_means = pd.DataFrame(X_scaled).groupby(clusters)[0].mean()
        sorted_clusters = cluster_means.sort_values(ascending=False).index.tolist()
        risk_map = {}
        for i, cluster_id in enumerate(sorted_clusters):
            risk_map[cluster_id] = ['HIGH', 'MEDIUM', 'LOW'][i] if i < 3 else 'LOW'

        risk_score_map = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
        ml_df['cluster'] = clusters
        ml_df['risk_level'] = [risk_map[c] for c in clusters]
        ml_df['risk_score'] = [risk_score_map[risk_map[c]] for c in clusters]
        ml_df['timestamp'] = datetime.now().isoformat()
        ml_df['project_key'] = project_key
        ml_df['source'] = 'jira'

        stats = generate_summary_statistics(ml_df)

        modules = []
        for _, row in ml_df.head(100).iterrows():
            modules.append({
                'module_id': row.get('module_name', 'Unknown'),
                'total_issues': int(row.get('total_issues', 0)),
                'defect_count': int(row.get('defect_count', 0)),
                'high_priority_defects': int(row.get('high_priority_defects', 0)),
                'avg_priority': float(row.get('avg_priority', 0)),
                'bug_rate': float(row.get('bug_rate', 0)),
                'risk_level': row.get('risk_level', 'UNKNOWN'),
                'risk_score': int(row.get('risk_score', 0))
            })

        # Save full analysis to MongoDB
        db = get_db()
        db_result = db.analyses.insert_one({
            'filename': f"JIRA:{project_key}",
            'source': 'jira',
            'project_key': project_key,
            'timestamp': datetime.now().isoformat(),
            'total_modules': stats['total_modules'],
            'high_risk': stats['high_risk'],
            'medium_risk': stats['medium_risk'],
            'low_risk': stats['low_risk'],
            'success_rate': stats['success_rate'],
            'user_id': session.get('user_id'),
            'username': session.get('username'),
            'features': feature_cols,
            'modules': modules
        })

        log_audit_action(
            session.get('user_id'),
            session.get('username'),
            'JIRA_ANALYZED',
            resource=project_key,
            ip_address=request.remote_addr,
            details=f"Analyzed JIRA project {project_key}"
        )

        output_csv = os.path.join(OUTPUT_FOLDER, f'jira_{project_key}_analysis.csv')
        ml_df.to_csv(output_csv, index=False)

        return jsonify({
            'success': True,
            'analysis_id': str(db_result.inserted_id),
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
    return render_template('error.html', error="404 Not Found",
                           message="The page you requested doesn't exist."), 404


@app.errorhandler(500)
def server_error(error):
    return render_template('error.html', error="500 Server Error",
                           message="An unexpected error occurred."), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429


# ==================== APPLICATION STARTUP ====================

if __name__ == "__main__":
    init_database()
    port = int(os.environ.get("PORT", 10000))
    if os.environ.get('FLASK_ENV') == 'production':
        logger.info(f"Starting production server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        logger.info(f"Starting development server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=True)
