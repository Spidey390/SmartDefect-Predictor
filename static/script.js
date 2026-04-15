let allModules = [];
let allFeatures = [];
let pieChart = null;
let barChart = null;
let jiraData = null;

const fileInput = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
const fileHint = document.getElementById('fileHint');
const analyzeBtn = document.getElementById('analyzeBtn');
const errorBox = document.getElementById('errorBox');
const loadingBox = document.getElementById('loadingBox');

fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
        fileHint.textContent = fileInput.files[0].name;
        analyzeBtn.disabled = false;
    }
});

dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('drag-over');
    dropzone.style.borderColor = 'var(--accent)';
    dropzone.style.background = 'var(--surface-light)';
});

dropzone.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    dropzone.style.borderColor = 'var(--border)';
    dropzone.style.background = 'var(--surface)';
});

dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    dropzone.style.borderColor = 'var(--border)';
    dropzone.style.background = 'var(--surface)';
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.csv')) {
        fileInput.files = e.dataTransfer.files;
        fileHint.textContent = file.name;
        analyzeBtn.disabled = false;
        notify('File loaded: ' + file.name, 'success');
    } else {
        notify('Please upload a CSV file', 'error');
    }
});
dropzone.addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    fileInput.click();
});

function showError(msg) {
    errorBox.textContent = '⚠ ' + msg;
    errorBox.classList.remove('hidden');
}

function hideError() {
    errorBox.classList.add('hidden');
}

// Professional toast notification system
function notify(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.style.cssText = `position:fixed; bottom:20px; right:20px; padding:15px 25px; border-radius:8px; background:var(--surface-light); border-left:4px solid ${type === 'success' ? 'var(--low)' : 'var(--high)'}; z-index:9999; animation: slideIn 0.3s ease-out; color:var(--text-main); font-family:'Syne',sans-serif; font-weight:600;`;
    toast.innerHTML = `<strong>${type.toUpperCase()}</strong>: ${message}`;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

async function uploadFile() {
    if (!fileInput.files.length) return;
    hideError();

    // Show loading overlay
    const loadingOverlay = document.getElementById('loadingOverlay');
    if (loadingOverlay) {
        loadingOverlay.classList.add('active');
    } else {
        loadingBox.classList.remove('hidden');
    }

    analyzeBtn.disabled = true;
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (loadingOverlay) {
            loadingOverlay.classList.remove('active');
        } else {
            loadingBox.classList.add('hidden');
        }

        if (data.error) {
            showError(data.error);
            analyzeBtn.disabled = false;
            notify('Upload failed: ' + data.error, 'error');
            return;
        }

        allModules = data.modules;
        allFeatures = data.features;
        renderDashboard(data.stats, data.modules, data.features);
        showSection('dashboard');
        loadHistory();
        notify('Analysis complete! ' + data.stats.total + ' modules processed', 'success');
    } catch (e) {
        if (loadingOverlay) {
            loadingOverlay.classList.remove('active');
        } else {
            loadingBox.classList.add('hidden');
        }
        showError('Server error: ' + e.message);
        analyzeBtn.disabled = false;
        notify('Upload error: ' + e.message, 'error');
    }
}

function renderDashboard(stats, modules, features) {
    document.getElementById('statTotal').textContent = stats.total;
    document.getElementById('statHigh').textContent = stats.high;
    document.getElementById('statMedium').textContent = stats.medium;
    document.getElementById('statLow').textContent = stats.low;
    document.getElementById('statRate').textContent = stats.success_rate + '%';
    renderPieChart(stats);
    renderBarChart(stats);
    renderTable(modules, features);
}

function renderPieChart(stats) {
    if (pieChart) pieChart.destroy();
    const ctx = document.getElementById('pieChart').getContext('2d');
    pieChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['High Risk', 'Medium Risk', 'Low Risk'],
            datasets: [{
                data: [stats.high, stats.medium, stats.low],
                backgroundColor: ['rgba(255,59,107,0.85)', 'rgba(245,166,35,0.85)', 'rgba(0,229,160,0.85)'],
                borderColor: ['#ff3b6b', '#f5a623', '#00e5a0'],
                borderWidth: 2,
                hoverOffset: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '65%',
            plugins: {
                legend: { position: 'bottom', labels: { color: '#6e7091', font: { family: 'Syne', weight: '600', size: 12 }, padding: 16 } }
            }
        }
    });
}

function renderBarChart(stats) {
    if (barChart) barChart.destroy();
    const ctx = document.getElementById('barChart').getContext('2d');
    barChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['High Risk', 'Medium Risk', 'Low Risk'],
            datasets: [{
                label: 'Modules',
                data: [stats.high, stats.medium, stats.low],
                backgroundColor: ['rgba(255,59,107,0.3)', 'rgba(245,166,35,0.3)', 'rgba(0,229,160,0.3)'],
                borderColor: ['#ff3b6b', '#f5a623', '#00e5a0'],
                borderWidth: 2,
                borderRadius: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6e7091', font: { family: 'IBM Plex Mono', size: 12 } } },
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6e7091', font: { family: 'IBM Plex Mono', size: 12 } } }
            }
        }
    });
}

function renderTable(modules, features) {
    const head = document.getElementById('tableHead');
    const body = document.getElementById('tableBody');
    const cols = ['module_id', ...features, 'risk_level', 'risk_score'];
    head.innerHTML = '<tr>' + cols.map(c => `<th>${c.replace(/_/g, ' ')}</th>`).join('') + '</tr>';
    body.innerHTML = modules.map(m => {
        return '<tr>' + cols.map(c => {
            if (c === 'risk_level') return `<td><span class="badge badge-${m[c]}">${m[c]}</span></td>`;
            const val = m[c];
            return `<td>${typeof val === 'number' ? (Number.isInteger(val) ? val : val.toFixed(3)) : val}</td>`;
        }).join('') + '</tr>';
    }).join('');
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    const firstBtn = document.querySelector('.filter-btn:first-child');
    if (firstBtn) firstBtn.classList.add('active');
}

function filterTable(risk) {
    const filtered = risk === 'ALL' ? allModules : allModules.filter(m => m.risk_level === risk);
    renderTable(filtered, allFeatures);
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    const activeBtn = [...document.querySelectorAll('.filter-btn')].find(b => b.textContent.trim() === (risk === 'ALL' ? 'All' : risk.charAt(0) + risk.slice(1).toLowerCase()));
    if (activeBtn) activeBtn.classList.add('active');
}

async function loadHistory() {
    try {
        const res = await fetch('/api/history');
        const rows = await res.json();
        const body = document.getElementById('historyBody');
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="9" class="empty-row">No history yet.</td></tr>';
            return;
        }
        body.innerHTML = rows.map((r, idx) => {
            const ts = new Date(r.timestamp).toLocaleString();
            const sourceIcon = r.source === 'jira' ? '🔗 JIRA' : '📄 CSV';
            return `<tr onclick="viewHistoryDetail('${r.id}')" style="cursor:pointer;" title="Click to view analysis details">
<td>${idx + 1}</td>
<td><span style="font-size:0.8rem;opacity:0.8;">${sourceIcon}</span></td>
<td>${r.filename}</td>
<td>${ts}</td>
<td>${r.total_modules}</td>
<td style="color:var(--high)">${r.high_risk}</td>
<td style="color:var(--medium)">${r.medium_risk}</td>
<td style="color:var(--low)">${r.low_risk}</td>
<td style="color:var(--rate)">${r.success_rate}%</td>
</tr>`;
        }).join('');
    } catch (e) {
        console.error('History load error:', e);
    }
}

async function viewHistoryDetail(analysisId) {
    try {
        const res = await fetch(`/api/history/${analysisId}`);
        const data = await res.json();
        if (data.error) { alert('Could not load analysis: ' + data.error); return; }

        allModules = data.modules;
        allFeatures = data.features;

        renderDashboard(data.stats, data.modules, data.features);

        // Switch to the dashboard section and scroll to it
        showSection('dashboard');
        document.getElementById('dashboard').scrollIntoView({ behavior: 'smooth' });

        // Show a banner indicating this is a historical run
        const header = document.querySelector('#dashboard .section-header h2');
        if (header) {
            header.innerHTML = `Analysis <span class="accent-glow">Results</span> <span style="font-size:0.7em;color:#6e7091;font-weight:400;">— history: ${data.filename}</span>`;
        }
    } catch (e) {
        alert('Error loading history detail: ' + e.message);
    }
}

async function downloadFile(type) {
    window.location.href = '/download/' + type;
}

// ==================== JIRA CONFIG ====================

async function loadSavedJiraConfig() {
    try {
        const res = await fetch('/api/user/jira-config');
        const data = await res.json();
        if (data.success && data.config) {
            const cfg = data.config;
            if (cfg.jira_url) document.getElementById('jiraUrl').value = cfg.jira_url;
            if (cfg.jira_email) document.getElementById('jiraEmail').value = cfg.jira_email;
            if (cfg.jira_token) document.getElementById('jiraToken').value = cfg.jira_token;
        }
    } catch (e) {
        console.error('Could not load JIRA config:', e);
    }
}

async function saveJiraCredentials() {
    const creds = getJiraCredentials();
    if (!creds.jira_url || !creds.email || !creds.api_token) {
        showJiraStatus('Please fill in JIRA URL, Email, and API Token before saving.', 'error');
        return;
    }
    const btn = document.getElementById('saveJiraBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Saving...';
    try {
        const res = await fetch('/api/user/jira-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jira_url: creds.jira_url, email: creds.email, api_token: creds.api_token })
        });
        const data = await res.json();
        if (data.success) {
            showJiraStatus('✓ JIRA credentials saved successfully!', 'success');
        } else {
            showJiraStatus(`✗ ${data.error}`, 'error');
        }
    } catch (e) {
        showJiraStatus(`Error saving credentials: ${e.message}`, 'error');
    }
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">💾</span> Save Credentials';
}

// ==================== JIRA ANALYSIS FUNCTIONS ====================

const jiraStatusBox = document.getElementById('jiraStatusBox');
const jiraAnalyzeBtn = document.getElementById('jiraAnalyzeBtn');

function showJiraStatus(message, type = 'info') {
    jiraStatusBox.textContent = message;
    jiraStatusBox.className = 'status-box ' + type;
    jiraStatusBox.classList.remove('hidden');
}

function hideJiraStatus() {
    jiraStatusBox.classList.add('hidden');
}

function getJiraCredentials() {
    return {
        jira_url: document.getElementById('jiraUrl').value.trim(),
        email: document.getElementById('jiraEmail').value.trim(),
        api_token: document.getElementById('jiraToken').value.trim(),
        project_key: document.getElementById('projectKey').value.trim().toUpperCase(),
        max_issues: parseInt(document.getElementById('maxIssues').value) || 1000,
        bugs_only: document.getElementById('bugsOnly').checked
    };
}

async function testJiraConnection() {
    const creds = getJiraCredentials();

    if (!creds.jira_url || !creds.email || !creds.api_token) {
        showJiraStatus('Please fill in JIRA URL, Email, and API Token', 'error');
        return;
    }

    showJiraStatus('Testing connection...', 'info');
    jiraAnalyzeBtn.disabled = true;

    try {
        const res = await fetch('/api/jira/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(creds)
        });

        const data = await res.json();

        if (data.success) {
            showJiraStatus(`✓ Connected: ${data.message}`, 'success');
        } else {
            showJiraStatus(`✗ ${data.message}`, 'error');
        }
    } catch (e) {
        showJiraStatus(`Connection error: ${e.message}`, 'error');
    }

    jiraAnalyzeBtn.disabled = false;
}

async function loadProjects() {
    const creds = getJiraCredentials();

    if (!creds.jira_url || !creds.email || !creds.api_token) {
        showJiraStatus('Please fill in JIRA credentials to load projects', 'error');
        return;
    }

    const btn = document.getElementById('loadProjectsBtn');
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        const params = new URLSearchParams({
            jira_url: creds.jira_url,
            email: creds.email,
            api_token: creds.api_token
        });

        const res = await fetch(`/api/jira/projects?${params}`);
        const data = await res.json();

        const projectList = document.getElementById('projectList');

        if (data.success && data.projects.length > 0) {
            projectList.innerHTML = data.projects.map(p => `
                <div class="project-item" onclick="selectProject('${p.key}')">
                    <span class="project-key">${p.key}</span>
                    <span class="project-name">${p.name}</span>
                </div>
            `).join('');
            projectList.classList.remove('hidden');
            showJiraStatus(`Found ${data.projects.length} projects`, 'success');
        } else {
            projectList.classList.add('hidden');
            showJiraStatus(data.error || 'No projects found', 'error');
        }
    } catch (e) {
        showJiraStatus(`Error loading projects: ${e.message}`, 'error');
    }

    btn.disabled = false;
    btn.textContent = 'Load Projects';
}

function selectProject(key) {
    document.getElementById('projectKey').value = key;
    document.getElementById('projectList').classList.add('hidden');
}

async function analyzeJiraProject() {
    const creds = getJiraCredentials();

    if (!creds.jira_url || !creds.email || !creds.api_token) {
        showJiraStatus('Please fill in JIRA credentials', 'error');
        return;
    }

    if (!creds.project_key) {
        showJiraStatus('Please enter a project key', 'error');
        return;
    }

    hideJiraStatus();

    // Show loading overlay
    const loadingOverlay = document.getElementById('loadingOverlay');
    if (loadingOverlay) {
        loadingOverlay.classList.add('active');
    }

    jiraAnalyzeBtn.disabled = true;
    jiraAnalyzeBtn.innerHTML = '<span class="spinner" style="width:16px;height:16px;border-width:2px;"></span> Analyzing...';

    try {
        const res = await fetch('/api/jira/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(creds)
        });

        const data = await res.json();

        if (loadingOverlay) {
            loadingOverlay.classList.remove('active');
        }

        if (data.error) {
            showJiraStatus(`✗ ${data.error}`, 'error');
            jiraAnalyzeBtn.disabled = false;
            jiraAnalyzeBtn.innerHTML = '<span class="btn-icon">🤖</span> Analyze Project';
            notify('JIRA analysis failed: ' + data.error, 'error');
            return;
        }

        showJiraStatus(`✓ Analysis complete! Found ${data.total_issues_fetched} issues, ${data.stats.total_modules} modules analyzed`, 'success');
        notify(`JIRA analysis complete: ${data.stats.total_modules} modules`, 'success');

        jiraData = data;
        allModules = data.modules;
        allFeatures = data.features;

        renderDashboard(data.stats, data.modules, data.features);
        showSection('dashboard');
        document.getElementById('dashboard').scrollIntoView({ behavior: 'smooth' });
        loadHistory();

    } catch (e) {
        if (loadingOverlay) {
            loadingOverlay.classList.remove('active');
        }
        showJiraStatus(`Error: ${e.message}`, 'error');
        notify('JIRA analysis error: ' + e.message, 'error');
    }

    jiraAnalyzeBtn.disabled = false;
    jiraAnalyzeBtn.innerHTML = '<span class="btn-icon">🤖</span> Analyze Project';
}

// ==================== NAVIGATION ====================

function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'));
    const target = document.getElementById(sectionId);
    if (target) target.classList.remove('hidden');

    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    const link = document.querySelector(`.nav-link[href="#${sectionId}"]`);
    if (link) link.classList.add('active');
}

document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
        const target = link.getAttribute('href');
        if (target && target.startsWith('#')) {
            e.preventDefault();
            showSection(target.slice(1));
        }
    });
});

// ==================== INIT ====================

loadHistory();
loadSavedJiraConfig();
