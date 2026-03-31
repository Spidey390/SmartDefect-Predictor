let allModules = [];
let allFeatures = [];
let pieChart = null;
let barChart = null;
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
});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.csv')) {
        fileInput.files = e.dataTransfer.files;
        fileHint.textContent = file.name;
        analyzeBtn.disabled = false;
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
async function uploadFile() {
    if (!fileInput.files.length) return;
    hideError();
    loadingBox.classList.remove('hidden');
    analyzeBtn.disabled = true;
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();
        loadingBox.classList.add('hidden');
        if (data.error) { showError(data.error); analyzeBtn.disabled = false; return; }
        allModules = data.modules;
        allFeatures = data.features;
        renderDashboard(data.stats, data.modules, data.features);
        document.getElementById('dashboard').classList.remove('hidden');
        document.getElementById('dashboard').scrollIntoView({ behavior: 'smooth' });
        loadHistory();
    } catch (e) {
        loadingBox.classList.add('hidden');
        showError('Server error: ' + e.message);
        analyzeBtn.disabled = false;
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
    document.querySelector('.filter-btn:first-child').classList.add('active');
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
        const res = await fetch('/history');
        const rows = await res.json();
        const body = document.getElementById('historyBody');
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="8" class="empty-row">No history yet.</td></tr>';
            return;
        }
        body.innerHTML = rows.map(r => {
            const ts = new Date(r.timestamp).toLocaleString();
            return `<tr>
<td>${r.id}</td>
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
async function downloadFile(type) {
    window.location.href = '/download/' + type;
}
document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', () => {
        document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
        link.classList.add('active');
    });
});
loadHistory();
