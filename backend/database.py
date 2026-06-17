import sqlite3
import os
from backend.config import DATABASE_PATH


def get_db():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def get_db_connection():
    """Get a direct database connection (non-generator)."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database tables."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user' CHECK(role IN ('admin', 'user')),
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Agreements table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'active', 'expired', 'terminated')),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Agreement Analysis table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agreement_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id INTEGER UNIQUE NOT NULL,
            company_name TEXT,
            agreement_date TEXT,
            consulting_start_date TEXT,
            consulting_end_date TEXT,
            payment_type TEXT,
            payment_amount REAL,
            payment_frequency TEXT,
            payment_schedule TEXT,
            raw_text TEXT,
            summary TEXT,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        )
    """)

    # Payments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'paid', 'overdue')),
            paid_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        )
    """)

    # Notifications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agreement_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            type TEXT DEFAULT 'info' CHECK(type IN ('info', 'warning', 'alert')),
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE SET NULL
        )
    """)

    # Email Settings table (Gmail OAuth2 credentials and template)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_client_id TEXT,
            gmail_client_secret_encrypted TEXT,
            gmail_refresh_token_encrypted TEXT,
            sender_email TEXT,
            cc_emails TEXT,
            email_subject TEXT DEFAULT 'Payment Reminder — {{company_name}}',
            email_template_type TEXT DEFAULT 'text' CHECK(email_template_type IN ('text', 'html')),
            email_template TEXT,
            is_enabled INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Email Log table (track sent emails to prevent duplicates)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL,
            agreement_id INTEGER NOT NULL,
            recipient_email TEXT NOT NULL,
            subject TEXT,
            status TEXT DEFAULT 'sent',
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        )
    """)

    # Migrate agreements table — add is_viewed column for "New" badge
    try:
        cursor.execute("ALTER TABLE agreements ADD COLUMN is_viewed INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists

    # Migrate agreements table — add renewal_status column
    try:
        cursor.execute("ALTER TABLE agreements ADD COLUMN renewal_status TEXT DEFAULT NULL")
    except Exception:
        pass  # Column already exists

    # Migrate agreements table — add renewal_increase_percent column
    try:
        cursor.execute("ALTER TABLE agreements ADD COLUMN renewal_increase_percent REAL DEFAULT 10")
    except Exception:
        pass  # Column already exists

    # Migrate agreement_analysis table — add new columns for deep analysis
    new_columns = [
        # Agreement Overview
        ("agreement_title", "TEXT"),
        ("agreement_type", "TEXT"),
        ("contact_person", "TEXT"),
        ("effective_date", "TEXT"),
        ("expiry_date", "TEXT"),
        ("priority_level", "TEXT"),
        ("auto_renewal", "TEXT"),
        ("currency", "TEXT DEFAULT '₹'"),
        # Company Information
        ("industry", "TEXT"),
        ("website", "TEXT"),
        ("gst_number", "TEXT"),
        ("company_size", "TEXT"),
        ("email", "TEXT"),
        ("phone", "TEXT"),
        ("alternate_contact", "TEXT"),
        # Timeline
        ("approved_date", "TEXT"),
        ("signed_date", "TEXT"),
        ("active_date", "TEXT"),
        ("renewal_due_date", "TEXT"),
        # Payment Structure
        ("payment_method", "TEXT"),
        ("remaining_balance", "REAL"),
        ("next_due_date", "TEXT"),
        ("late_fee_policy", "TEXT"),
        ("payment_plans", "TEXT"),
        # Consulting Visit Schedule
        ("consulting_visits", "TEXT"),
        # Legal Clauses
        ("nda_included", "TEXT"),
        ("non_solicitation", "TEXT"),
        ("non_compete", "TEXT"),
        ("confidentiality_clause", "TEXT"),
        ("data_protection_clause", "TEXT"),
        ("arbitration_clause", "TEXT"),
        ("jurisdiction", "TEXT"),
        # Services
        ("services", "TEXT"),
    ]

    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE agreement_analysis ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # Column already exists

    # Migrate email_settings table — add email_template_html column for separate HTML template storage
    try:
        cursor.execute("ALTER TABLE email_settings ADD COLUMN email_template_html TEXT")
    except Exception:
        pass  # Column already exists

    # Migrate users table — add global_payment_access column (HR role)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN global_payment_access INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists

    conn.commit()

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agreements_user_id ON agreements(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_agreement_id ON agreement_analysis(agreement_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_agreement_id ON payments(agreement_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id)")

    conn.commit()

    # Create default admin if not exists
    from passlib.hash import bcrypt
    admin_exists = cursor.execute("SELECT id FROM users WHERE role='admin'").fetchone()
    if not admin_exists:
        admin_hash = bcrypt.hash("admin123")
        cursor.execute(
            "INSERT INTO users (username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("admin", "admin@dvconsulting.co.in", "System Administrator", admin_hash, "admin")
        )
        conn.commit()

    conn.close()
