from flask import Flask, request, jsonify, send_file, render_template
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import sqlite3
import os
from datetime import datetime
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
if __name__ == '__main__':
    app.run(debug=True)
