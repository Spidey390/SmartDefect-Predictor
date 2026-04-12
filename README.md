# SmartDefect Predictor

A production-ready Flask web application for predicting high-risk software modules using KMeans clustering. Features JIRA integration for real-time defect analysis and role-based access control.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![Flask](https://img.shields.io/badge/flask-2.0+-green.svg)

## Features

- **CSV Upload Analysis**: Upload defect datasets and get instant risk classifications (High/Medium/Low)
- **JIRA Integration**: Connect to JIRA API v3 to fetch and analyze real project issues
- **ML-Powered**: KMeans clustering engine for accurate risk prediction
- **Role-Based Access**: Separate Admin and User dashboards with authentication
- **Audit Logging**: Complete security audit trail for all user actions
- **Production Ready**: Rate limiting, security headers, session management

## Quick Start

### 1. Clone and Setup

```bash
cd SmartDefect-Predictor
python -m venv venv
venv\Scripts\activate  # Windows
# or
source venv/bin/activate  # Linux/Mac
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required configuration:
- `SECRET_KEY`: Generate with `python -c "import os; print(os.urandom(32).hex())"`
- `JIRA_URL`: Your JIRA instance URL (e.g., `https://your-domain.atlassian.net`)
- `JIRA_EMAIL`: Your JIRA email
- `JIRA_API_TOKEN`: Get from [Atlassian](https://id.atlassian.com/manage/api-tokens)

### 4. Run the Application

```bash
python app.py
```

Open http://localhost:10000 in your browser.

## Default Credentials

| Role  | Username | Password   |
|-------|----------|------------|
| Admin | `admin`  | `admin123` |
| User  | `user`   | `user123`  |

**Important**: Change these credentials in production!

## Project Structure

```
SmartDefect-Predictor/
├── app.py                  # Main Flask application
├── services/
│   ├── auth_service.py     # Authentication & authorization
│   ├── jira_service.py     # JIRA API integration (v3)
│   └── data_processor.py   # ML data processing
├── templates/
│   ├── login.html          # Login page
│   ├── register.html       # Registration page
│   ├── user_dashboard.html # User analysis dashboard
│   ├── admin_dashboard.html# Admin management panel
│   ├── admin_users.html    # User management
│   ├── admin_audit.html    # Audit log viewer
│   ├── admin_settings.html # System settings
│   ├── profile.html        # User profile
│   ├── history.html        # Analysis history
│   └── error.html          # Error pages
├── static/
│   ├── style.css           # Application styles
│   └── script.js           # Frontend JavaScript
├── uploads/                # Uploaded CSV files
├── outputs/                # Generated reports
├── database.db             # SQLite database
├── requirements.txt        # Python dependencies
├── .env.example            # Environment template
└── README.md
```

## API Endpoints

### Authentication
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login` | GET | Login page |
| `/register` | GET | Registration page |
| `/api/auth/login` | POST | Authenticate user |
| `/api/auth/register` | POST | Register new user |
| `/api/auth/logout` | GET/POST | Logout user |
| `/api/auth/me` | GET | Get current user |

### Analysis
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload` | POST | Upload CSV for analysis |
| `/api/history` | GET | Get analysis history |
| `/download/csv` | GET | Download results CSV |
| `/download/powerbi` | GET | Download Power BI export |

### JIRA Integration
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/jira/test` | POST | Test JIRA connection |
| `/api/jira/analyze` | POST | Analyze JIRA project |
| `/api/jira/projects` | GET | List JIRA projects |

### Admin (Admin Only)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/dashboard` | GET | Admin dashboard |
| `/admin/users` | GET | User management |
| `/admin/audit` | GET | Audit log |
| `/admin/settings` | GET | System settings |
| `/api/admin/stats` | GET | Dashboard statistics |
| `/api/admin/users` | GET | List all users |
| `/api/admin/recent-activity` | GET | Recent audit activity |

## JIRA API v3 Integration

This application uses the **JIRA API v3** endpoints:
- `/rest/api/3/search/jql` - Primary search endpoint
- `/rest/api/3/search` - Fallback endpoint
- `/rest/api/3/project` - Project listing

### Getting Your JIRA API Token

1. Go to https://id.atlassian.com/manage/api-tokens
2. Click "Create API token"
3. Label your token (e.g., "SmartDefect Predictor")
4. Copy the token and add it to your `.env` file

## Production Deployment

### Using Gunicorn

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:10000 app:app
```

### Environment Variables for Production

```bash
FLASK_ENV=production
SECRET_KEY=<strong-random-key-50-chars>
PORT=10000
```

### Security Features

- **Session Security**: HTTP-only cookies, SameSite protection
- **Rate Limiting**: 50 requests/hour per IP on API endpoints
- **Security Headers**: X-Frame-Options, X-XSS-Protection, HSTS
- **Password Hashing**: SHA-256 with salt
- **Audit Logging**: All user actions tracked

## Database Schema

### Users Table
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT,
    role TEXT DEFAULT 'user',
    created_at TEXT,
    last_login TEXT,
    is_active INTEGER DEFAULT 1
);
```

### Analyses Table
```sql
CREATE TABLE analyses (
    id INTEGER PRIMARY KEY,
    filename TEXT,
    timestamp TEXT,
    total_modules INTEGER,
    high_risk INTEGER,
    medium_risk INTEGER,
    low_risk INTEGER,
    success_rate REAL,
    user_id INTEGER
);
```

### Audit Log Table
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    username TEXT,
    action TEXT,
    resource TEXT,
    ip_address TEXT,
    timestamp TEXT,
    details TEXT
);
```

## ML Algorithm

The application uses **KMeans clustering** (k=3) to classify modules:

1. **Feature Extraction**: Numeric columns from CSV (defect counts, priorities, etc.)
2. **Standardization**: Z-score normalization
3. **Clustering**: KMeans with 3 clusters
4. **Risk Mapping**: Clusters mapped to HIGH/MEDIUM/LOW based on defect density

### Input CSV Format

Your CSV should have:
- Module identifiers (names/IDs)
- Numeric metrics (defect counts, complexity, etc.)
- At least one column with "defect" in the name (optional)

## Troubleshooting

### JIRA Connection Fails
- Verify your JIRA URL (should be `https://domain.atlassian.net`)
- Check API token is valid
- Ensure email matches your Atlassian account

### Port Already in Use
```bash
# Change port in .env
PORT=8080
```

### Database Errors
```bash
# Delete and recreate database
rm database.db
python -c "from app import init_database; init_database()"
```

## License

MIT License - See LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

---

Built with Flask, scikit-learn, and Chart.js
