# Agreement Management System

AI-powered Consulting Agreement Management System for internal consulting company usage.

## Features

- **Agreement Upload & Analysis**: Upload PDF/DOCX agreements, AI extracts structured data using Llama 3.3 70B
- **Payment Tracking**: Automatic payment schedule generation with mark-paid functionality
- **Timeline Visualization**: Professional agreement timeline view
- **Search & Filter**: Fast search by company name, ID, status, payment type
- **Notifications**: Upcoming expiry and payment due alerts
- **Admin Panel**: User management, system monitoring, all-agreements view
- **JWT Authentication**: Secure custom auth with role-based access control

## Tech Stack

- **Backend**: Python, FastAPI, SQLite
- **Frontend**: HTML, CSS, JavaScript
- **AI**: Groq API (Llama 3.3 70B Versatile)
- **Auth**: Custom JWT with bcrypt password hashing
- **Deployment**: Railway

## Quick Start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Create `.env` file (see `.env.example`):
   ```
   GROQ_API_KEY=your_groq_api_key
   ```

3. Run the application:
   ```bash
   python main.py
   ```

4. Open `http://localhost:8000` in your browser

## Default Admin Credentials

- **Username**: `admin`
- **Password**: `admin123`

> Change the admin password immediately after first login.

## Project Structure

```
├── main.py                  # FastAPI application entry point
├── requirements.txt         # Python dependencies
├── Procfile                 # Railway deployment
├── railway.toml             # Railway configuration
├── backend/
│   ├── config.py            # Application configuration
│   ├── database.py          # SQLite database setup & initialization
│   ├── auth.py              # JWT authentication & authorization
│   ├── ai_service.py        # Groq AI agreement analysis
│   ├── file_utils.py        # PDF/DOCX text extraction
│   └── routes/
│       ├── auth_routes.py       # Login, register, profile
│       ├── agreement_routes.py  # CRUD, upload, analysis, payments
│       ├── admin_routes.py      # Admin dashboard & user management
│       └── notification_routes.py  # Notification management
├── frontend/
│   ├── styles.css           # Design system (enterprise white theme)
│   ├── app.js               # Shared utilities & API client
│   ├── login.html           # Login page
│   ├── register.html        # Registration page
│   ├── dashboard.html       # User dashboard
│   ├── agreement.html       # Agreement detail & analysis
│   └── admin.html           # Admin panel
└── uploads/                 # File storage (auto-created)
```

## Deployment to Railway

1. Push code to GitHub
2. Connect repository in Railway
3. Set environment variables:
   - `GROQ_API_KEY`
   - `SECRET_KEY` (use a strong random string)
4. Deploy

## Security

- Passwords hashed with bcrypt
- JWT token authentication on all API routes
- Ownership validation on all agreement operations
- File type and size validation
- SQL injection prevention via parameterized queries
- Role-based access control (Admin/User)
