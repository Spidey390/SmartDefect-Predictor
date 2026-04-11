from flask import Flask, request, jsonify, send_file, render_template
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv
from services.jira_service import JiraService, extract_issue_data
from services.data_processor import (
    group_by_component,
    create_ml_dataset,
    prepare_features_for_ml,
    add_risk_predictions,
    generate_summary_statistics
)

load_dotenv()
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
DB_PATH = 'database.db'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
def init_db():
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
        success_rate REAL
    )''')
    conn.commit()
    conn.close()
init_db()
@app.route('/')
def index():
    return render_template('index.html')
@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)
    try:
        df = pd.read_csv(filepath)
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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO analyses (filename, timestamp, total_modules, high_risk, medium_risk, low_risk, success_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (file.filename, datetime.now().isoformat(), total, high, medium, low, success_rate))
        conn.commit()
        conn.close()
        modules = df_clean[['module_id'] + feature_cols + ['risk_level', 'risk_score']].head(100).to_dict(orient='records')
        return jsonify({
            'success': True,
            'stats': {'total': total, 'high': high, 'medium': medium, 'low': low, 'success_rate': success_rate},
            'modules': modules,
            'features': feature_cols
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/history')
def history():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM analyses ORDER BY id DESC LIMIT 10')
    rows = c.fetchall()
    conn.close()
    keys = ['id', 'filename', 'timestamp', 'total_modules', 'high_risk', 'medium_risk', 'low_risk', 'success_rate']
    return jsonify([dict(zip(keys, row)) for row in rows])
@app.route('/download/csv')
def download_csv():
    path = os.path.join(OUTPUT_FOLDER, 'defects_with_risk.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'No results yet. Upload a file first.'}), 404
    return send_file(path, as_attachment=True)
@app.route('/download/powerbi')
def download_powerbi():
    path = os.path.join(OUTPUT_FOLDER, 'powerbi_export.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'No results yet. Upload a file first.'}), 404
    return send_file(path, as_attachment=True)


# ==================== JIRA API ENDPOINTS ====================

@app.route('/api/jira/test', methods=['POST'])
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
def analyze_jira_project():
    """
    Analyze a JIRA project for defect prediction

    Expected JSON body:
    {
        "jira_url": "https://your-domain.atlassian.net",
        "email": "your-email@example.com",
        "api_token": "your-api-token",
        "project_key": "PROJ",
        "include_bugs_only": false,
        "max_issues": 1000
    }
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
            # Group by issue type instead
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
            (filename, timestamp, total_modules, high_risk, medium_risk, low_risk, success_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (f"JIRA:{project_key}", datetime.now().isoformat(),
             stats['total_modules'], stats['high_risk'],
             stats['medium_risk'], stats['low_risk'], stats['success_rate']))
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
        return jsonify({'error': str(e)}), 500


@app.route('/api/jira/projects', methods=['GET'])
def get_jira_projects():
    """Get list of accessible JIRA projects"""
    jira_url = request.args.get('jira_url', os.getenv('JIRA_URL'))
    email = request.args.get('email', os.getenv('JIRA_EMAIL'))
    api_token = request.args.get('api_token', os.getenv('JIRA_API_TOKEN'))

    if not all([jira_url, email, api_token]):
        return jsonify({'error': 'Missing JIRA credentials'}), 400

    try:
        jira = JiraService(jira_url, email, api_token)
        response = jira.session.get(f'{jira_url}/rest/api/3/project')

        if response.status_code == 200:
            projects = response.json()
            return jsonify({
                'success': True,
                'projects': [
                    {'key': p.get('key'), 'name': p.get('name'), 'type': p.get('projectTypeKey')}
                    for p in projects
                ]
            })
        return jsonify({'error': f'Failed to fetch projects: {response.status_code}'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
