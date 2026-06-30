# =====================================================================================
# app/dashboard_server.py
# LIGHTWEIGHT WEB DASHBOARD — serves performance_dashboard.html + JSON via Flask
#
# Railway exposes this on the PORT env var (default 8080).
# Access via: https://your-app.railway.app/
# =====================================================================================
import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import threading
from flask import Flask, jsonify, send_file, send_from_directory, Response, request, make_response

from flask import session, redirect, url_for, abort, g
from functools import wraps
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import timedelta
import uuid
import database

# Ensure tzcache writable location before importing yfinance (robust import to support different cwd)
try:
    import app.yf_bootstrap
except Exception:
    try:
        import yf_bootstrap
    except Exception:
        pass
import yfinance as yf
from yf_rate_limiter import CircuitOpenError, acquire as yf_acquire, release as yf_release
from data_fetch_status import mark_success, mark_failure

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def serialize_datetimes(obj):
    if isinstance(obj, dict):
        return {k: serialize_datetimes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_datetimes(i) for i in obj]
    elif isinstance(obj, datetime):
        return obj.astimezone(IST).isoformat()
    return obj


try:
    from config import DATA_DIR, BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")

APP_DIR        = os.path.dirname(os.path.abspath(__file__))
PERF_JSON_PATH = os.path.join(DATA_DIR, "performance_data.json")

# ── Locate the dashboard HTML ────────────────────────────────────────────────────────
def get_html_path(filename):
    candidates = [
        os.path.join(APP_DIR, filename),
        os.path.join(BASE_DIR, filename),
    ]
    return next((p for p in candidates if os.path.exists(p)), None)

USER_DASHBOARD_PATH = get_html_path("user_dashboard.html")
ADMIN_DASHBOARD_PATH = get_html_path("admin_dashboard.html")


from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
# Tell Flask it is behind a reverse proxy (Railway) so it sets the secure cookie on HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.secret_key = os.getenv("SECRET_KEY", "fallback_dev_key_if_missing_in_prod")
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.getenv("FLASK_ENV") == "production"
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=6)

app.config['WTF_CSRF_CHECK_DEFAULT'] = False
csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100000 per day", "5000 per hour"],
    storage_uri="memory://"
)

# ── Auth Decorators ──────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or 'session_token' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
            
        if not database.check_session_validity(session['user_id'], session['session_token']):
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Session expired or revoked'}), 401
            return redirect('/login')
            
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or 'session_token' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
            
        if not database.check_session_validity(session['user_id'], session['session_token']):
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Session expired or revoked'}), 401
            return redirect('/login')
            
        if session.get('role') != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden'}), 403
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def check_session_validity():
    # If the user is logged in, check if must_change_password
    if 'user_id' in session:
        # Prevent idle timeout tracking on static files or background api polls if desired, 
        # but standard Flask sessions just update timestamp on modify.
        session.modified = True
        
        # Check for profile completion intercept
        if session.get('must_change_password'):
            # Allow them to hit the complete_profile page, logout, and static assets
            if request.endpoint not in ('complete_profile', 'login', 'logout', 'static', 'get_csrf_token', 'favicon'):
                return redirect('/complete_profile')

# ── PWA Routes ───────────────────────────────────────────────
# IMPORTANT: Service worker MUST be served from the root path '/'
# to allow it to control ALL pages. If served from /static/,
# it can only control pages under /static/ which breaks the PWA.

@app.route("/service-worker.js")
def service_worker():
    """Serve the service worker from root so it has full-site scope."""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    response = send_from_directory(static_dir, "service-worker.js")
    # Must be no-cache so browsers always get the latest version
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response

@app.route("/manifest.json")
def manifest():
    """Serve the manifest from root for maximum compatibility."""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    response = send_from_directory(static_dir, "manifest.json")
    response.headers["Content-Type"] = "application/manifest+json"
    return response

@app.route("/api/push/vapid_public_key", methods=["GET"])
def vapid_public_key():
    """Returns the VAPID public key so the frontend can subscribe."""
    pub_key = os.getenv("VAPID_PUBLIC_KEY")
    if not pub_key:
        return jsonify({"error": "VAPID key not configured on server"}), 500
    return jsonify({"vapid_public_key": pub_key})

@app.route("/api/push/subscribe", methods=["POST"])
@login_required
@csrf.exempt
def push_subscribe():
    """Saves the user's push subscription."""
    sub_data = request.json
    if not sub_data or not sub_data.get("endpoint"):
        return jsonify({"error": "Invalid subscription data"}), 400
        
    keys = sub_data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    
    if not p256dh or not auth:
        return jsonify({"error": "Missing subscription keys"}), 400
        
    user_id = session.get('user_id')
    success = database.save_push_subscription(user_id, sub_data["endpoint"], p256dh, auth)
    
    if success:
        return jsonify({"success": True, "message": "Subscribed successfully"}), 201
    return jsonify({"error": "Database error"}), 500

# ── Auth Routes ──────────────────────────────────────────────────────────

@app.route("/api/csrf_token", methods=["GET"])
def get_csrf_token():
    from flask_wtf.csrf import generate_csrf
    return jsonify({'csrf_token': generate_csrf()})

@app.route("/login", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == "GET":
        path = get_html_path("login.html")
        return send_file(path) if path and os.path.exists(path) else "login.html missing"
        
    identifier = request.form.get("username", "").strip()
    password = request.form.get("password")
    
    if not identifier or not password:
        return jsonify({"error": "Missing credentials"}), 400
        
    user_data = database.verify_user(identifier, password)
    if user_data:
        if isinstance(user_data, dict) and user_data.get('error') == 'pending_approval':
            return jsonify({"error": "Account pending admin approval"}), 403
            
        session.clear() # Anti-fixation
        session.permanent = True
        session['user_id'] = user_data['user_id']
        session['username'] = user_data['username']
        session['role'] = user_data['role']
        session['must_change_password'] = user_data['must_change_password']
        session['session_token'] = user_data['session_token']
        
        if user_data['must_change_password']:
            return redirect('/complete_profile')
        if user_data['role'] == 'admin':
            return redirect('/admin')
        return redirect('/')
    
    # Generic error
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/signup", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("5 per minute", methods=["POST"])
def signup():
    if request.method == "GET":
        path = get_html_path("signup.html")
        return send_file(path) if path and os.path.exists(path) else "signup.html missing"
        
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    mobile = request.form.get("mobile", "").strip()
    password = request.form.get("password")
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    
    if not all([username, email, mobile, password]):
        return jsonify({"error": "All fields are required"}), 400
        
    import re
    if not re.match(r'^\d{10}$', mobile):
        return jsonify({"error": "Mobile number must be exactly 10 digits"}), 400
        
    try:
        user_id = database.create_user(username, email, mobile, password, first_name, last_name, role='user')
        if user_id:
            # Success. Do NOT log them in. 
            # For simplicity with fetch, just return 200 JSON with a success flag,
            # or rely on frontend to redirect to a 'pending' page or login page with a message.
            return jsonify({"success": True, "message": "Account created. Pending admin approval."}), 200
        
        # Duplicate or DB error
        return jsonify({"error": "Failed to create account"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/guest_chat", methods=["POST"])
@csrf.exempt
@limiter.limit("3 per minute")
def guest_chat():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    message = request.form.get("message", "").strip()
    
    if not name or not email or not message:
        return jsonify({"error": "Name, email, and message are required"}), 400
        
    import re
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({"error": "Invalid email address"}), 400
        
    try:
        # Save to database
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO global_notifications (type, title, message)
                    VALUES (%s, %s, %s)
                """, ('support', f"Support Request from {name}", f"Email: {email}\n\nMessage:\n{message}"))
            conn.commit()

        # Send to telegram
        from telegram_engine import queue_telegram_message
        telegram_msg = f"📩 <b>New Guest Message</b>\n\n👤 <b>Name:</b> {name}\n📧 <b>Email:</b> {email}\n💬 <b>Message:</b>\n{message}"
        queue_telegram_message(telegram_msg)
        
        return jsonify({"success": True, "message": "Message sent successfully!"}), 200
    except Exception as e:
        logger.exception(f"Guest chat error")
        return jsonify({"error": "Failed to send message"}), 500

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect('/login')

@app.route("/complete_profile", methods=["GET", "POST"])
@csrf.exempt
@login_required
def complete_profile():
    if not session.get('must_change_password'):
        return redirect('/')
        
    if request.method == "GET":
        path = get_html_path("complete_profile.html")
        return send_file(path) if path and os.path.exists(path) else "complete_profile.html missing"
        
    # Process the form
    username = request.form.get("username")
    email = request.form.get("email")
    mobile = request.form.get("mobile")
    first_name = request.form.get("first_name", "")
    last_name = request.form.get("last_name", "")
    new_password = request.form.get("new_password")
    
    if not all([username, email, mobile, new_password]):
        return jsonify({"error": "All fields are required"}), 400
        
    import re
    if not re.match(r'^\d{10}$', mobile):
        return jsonify({"error": "Mobile number must be exactly 10 digits"}), 400
        
    try:
        from werkzeug.security import generate_password_hash
        p_hash = generate_password_hash(new_password, method='scrypt')
        new_token = str(uuid.uuid4())
        
        with database.get_connection() as conn:
            with conn.cursor() as cur:
                # Enforce unique email/mobile before updating
                cur.execute("SELECT user_id FROM users WHERE (username = %s OR email = %s OR mobile = %s) AND user_id != %s", (username, email, mobile, session['user_id']))
                if cur.fetchone():
                    return jsonify({"error": "Error updating profile. Username/Email/Mobile already in use."}), 400

                cur.execute("""
                    UPDATE users 
                    SET username = %s, email = %s, mobile = %s, first_name = %s, last_name = %s, 
                        password_hash = %s, must_change_password = FALSE, session_token = %s
                    WHERE user_id = %s
                """, (username, email, mobile, first_name, last_name, p_hash, new_token, session['user_id']))
            conn.commit()
            
        session['must_change_password'] = False
        session['username'] = username
        session['session_token'] = new_token
        return redirect('/admin' if session['role'] == 'admin' else '/')
    except Exception as e:
        return jsonify({"error": "Error updating profile. Username/Email/Mobile may already be in use."}), 400



@app.route('/favicon.ico')
def favicon():
    # Return a transparent 1x1 GIF to perfectly satisfy all browsers and CDNs
    from flask import send_file
    import io
    gif_data = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01D\x00;'
    return send_file(io.BytesIO(gif_data), mimetype='image/gif')


# ── Disable Flask startup banner in production ───────────────────────────────────────
import logging as _logging
_logging.getLogger("werkzeug").setLevel(_logging.WARNING)

from database import (
    get_user_id_by_username, ping_user_session, cleanup_stale_sessions, get_online_users_and_history,
    send_user_message, get_user_messages, mark_user_messages_read, get_unread_message_counts
)

@app.route("/api/viewers", methods=["POST", "GET"])
@login_required
def api_viewers():
    """Tracks active viewers by IP and Name using DB. Cleans up inactive ones (>120s)."""
    # 1. First, mark any inactive sessions as offline
    cleanup_stale_sessions()

    # 2. If it's a heartbeat/ping, update or start their session
    if request.method == "POST":
        user_id = session.get("user_id")
        if user_id:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
            # Ping their session table
            ping_user_session(user_id, ip)

    # 3. Always return current state (online + history)
    stats = get_online_users_and_history()
    unread = get_unread_message_counts()
    
    return jsonify({
        "active_count": len(stats["online"]),
        "viewers": [u["username"] for u in stats["online"]],
        "history": stats["history"],
        "detailed_online": stats["online"],
        "unread_messages": unread
    })

@app.route("/api/messages", methods=["GET", "POST"])
@login_required
def api_messages():
    """Get or send messages for a specific user."""
    if request.method == "GET":
        user_name = request.args.get("user")
        if not user_name:
            return jsonify({"error": "Missing user parameter"}), 400
        
        user_id = get_user_id_by_username(user_name)
        if not user_id:
            return jsonify({"error": "User not found"}), 404
            
        messages = get_user_messages(user_id)
        return jsonify(messages)
        
    elif request.method == "POST":
        data = request.json or {}
        user_name = data.get("user")
        message = data.get("message")
        is_from_admin = data.get("is_from_admin", False)
        
        if not user_name or not message:
            return jsonify({"error": "Missing user or message"}), 400
            
        user_id = get_user_id_by_username(user_name)
        if not user_id:
            return jsonify({"error": "User not found"}), 404
            
        success = send_user_message(user_id, message, is_from_admin)
        if success:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "Failed to send message"}), 500

@app.route("/api/messages/read", methods=["POST"])
@login_required
def api_messages_read():
    """Mark messages as read for a specific user."""
    data = request.json or {}
    user_name = data.get("user")
    as_admin = data.get("as_admin", False)
    
    if not user_name:
        return jsonify({"error": "Missing user"}), 400
        
    user_id = get_user_id_by_username(user_name)
    if not user_id:
        return jsonify({"error": "User not found"}), 404
        
    success = mark_user_messages_read(user_id, as_admin)
    return jsonify({"status": "success" if success else "error"})


# =====================================================================================
# NOTIFICATIONS API
# =====================================================================================
@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('''
                    SELECT id, type, title, message, symbol, is_seen, created_at 
                    FROM global_notifications
                    ORDER BY created_at DESC
                    LIMIT 50
                ''')
                notifications = [dict(row) for row in cur.fetchall()]
                
                # Format timestamps
                for n in notifications:
                    if n.get('created_at'):
                        dt = n['created_at']
                        if dt.tzinfo is not None:
                            from zoneinfo import ZoneInfo
                            dt = dt.astimezone(ZoneInfo("Asia/Kolkata"))
                        n['created_at'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                
                return jsonify(notifications)
    except Exception as e:
        logger.exception(f"Error fetching notifications")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/mark_seen/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_seen(notif_id):
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE global_notifications SET is_seen = TRUE WHERE id = %s', (notif_id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception(f"Error marking notification as seen")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/mark_all_seen', methods=['POST'])
@login_required
def mark_all_notifications_seen():
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE global_notifications SET is_seen = TRUE WHERE is_seen = FALSE')
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception(f"Error marking all notifications as seen")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/clear_all', methods=['POST'])
@login_required
def clear_all_notifications():
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM global_notifications')
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception(f"Error clearing all notifications")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/clear/<int:id>', methods=['POST'])
@login_required
def clear_notification(id):
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM global_notifications WHERE id = %s', (id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception(f"Error clearing notification {id}")
        return jsonify({"error": str(e)}), 500

# ── CORS + cache headers on every response ──────────────────────────────────────────
@app.after_request
def add_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Cache-Control"]                = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"]                       = "no-cache"
    response.headers["X-Frame-Options"]              = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"]       = "nosniff"
    response.headers["Strict-Transport-Security"]    = "max-age=31536000; includeSubDomains"
    return response


@app.route("/")
@login_required
def index():
    """Serve the user dashboard HTML."""
    if USER_DASHBOARD_PATH and os.path.exists(USER_DASHBOARD_PATH):
        r = make_response(send_file(USER_DASHBOARD_PATH))
        r.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        r.headers['Pragma'] = 'no-cache'
        r.headers['Expires'] = '0'
        return r
    return Response(
        "<h2 style='font-family:monospace;color:#00e5a0;background:#0b0e14;margin:0;padding:40px'>"
        "⚠️ user_dashboard.html not found.</h2>",
        mimetype="text/html",
    )

@app.route("/admin")
@admin_required
def admin_index():
    """Serve the admin dashboard HTML."""
    if ADMIN_DASHBOARD_PATH and os.path.exists(ADMIN_DASHBOARD_PATH):
        r = make_response(send_file(ADMIN_DASHBOARD_PATH))
        r.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        r.headers['Pragma'] = 'no-cache'
        r.headers['Expires'] = '0'
        return r
    return Response(
        "<h2 style='font-family:monospace;color:#00e5a0;background:#0b0e14;margin:0;padding:40px'>"
        "⚠️ admin_dashboard.html not found.</h2>",
        mimetype="text/html",
    )


@app.route("/api/admin/users/search", methods=["GET"])
@admin_required
def api_admin_users_search():
    query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()
    try:
        from database import search_users
        users = search_users(query, status_filter)
        return jsonify({"users": users})
    except Exception as e:
        logger.exception(f"Error searching users")
        return jsonify({"error": "Failed to search users"}), 500


@app.route("/api/admin/users/reset_password", methods=["POST"])
@admin_required
@csrf.exempt
def api_admin_reset_password():
    data = request.json or {}
    user_id = data.get("user_id")
    new_password = data.get("new_password")
    force_change = data.get("force_change", False)
    if not user_id or not new_password:
        return jsonify({"error": "Missing user_id or new_password"}), 400
        
    try:
        from database import admin_reset_password
        success = admin_reset_password(user_id, new_password, force_change)
        if success:
            msg = "Password reset successfully. User must change it on next login." if force_change else "Password reset successfully."
            return jsonify({"success": True, "message": msg})
        else:
            return jsonify({"error": "Failed to reset password."}), 400
    except Exception as e:
        logger.exception(f"Error resetting password")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/data/performance_data.json")
@login_required
def performance_json():
    """Serve the latest performance JSON for the dashboard to fetch, loaded from DB."""
    try:
        from database import get_system_state
        val = get_system_state("performance_data")
        if val:
            import json
            # Explicit validation to ensure payload is not malformed
            parsed = json.loads(val)
            # Ensure required top-level keys exist
            required_keys = {"generated_at", "trades", "summary", "equity_curve", "monthly", "by_scanner", "by_category"}
            if required_keys.issubset(parsed.keys()):
                # Re-serialize to string since Response expects string/bytes
                return Response(val, mimetype="application/json")
            else:
                logger.error("❌ Performance data missing required keys. Using fallback.")
    except Exception as e:
        logger.exception(f"❌ Failed to load or parse performance data from DB: {e}")

    # Return empty-but-valid structure so dashboard doesn't fall back to demo data
    empty = {
        "generated_at": datetime.now(IST).isoformat(),
        "trades": [],
        "summary": {
            "total_alerts":    0,
            "win_rate":        0,
            "winners":         0,
            "losers":          0,
            "avg_return_pct":  0,
            "avg_win_pct":     0,
            "avg_loss_pct":    0,
            "expectancy":      0,
            "best_trade_pct":  0,
            "worst_trade_pct": 0,
            "open_positions":  0,
        },
        "equity_curve": [],
        "monthly":      [],
        "by_scanner":   {},
        "by_category":  {},
    }
    return jsonify(empty), 200


@app.route("/health")
def health():
    """Railway health-check endpoint."""
    perf_exists = False
    perf_age    = None
    try:
        from database import get_system_state
        val = get_system_state("performance_data")
        perf_exists = val is not None
        if perf_exists:
            data = json.loads(val)
            gen_at = data.get("generated_at")
            if gen_at:
                gen_dt = datetime.fromisoformat(gen_at)
                if gen_dt.tzinfo is None:
                    gen_dt = gen_dt.replace(tzinfo=IST)
                now_dt = datetime.now(IST)
                perf_age = round((now_dt - gen_dt).total_seconds() / 3600, 1)
    except Exception:
        logger.exception("❌ Health check failed to load/parse performance data")

    return jsonify({
        "status":            "ok",
        "time_ist":          datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "performance_ready": perf_exists,
        "performance_age_h": perf_age,
    })


@app.route("/fyers/login")
@admin_required
def fyers_login():
    """Redirect admin user to Fyers OAuth authentication portal."""
    try:
        from fyers_auth import get_login_url
        login_url = get_login_url()
        return redirect(login_url)
    except Exception as e:
        logger.exception(f"Fyers login URL generation failed")
        return f"Error generating Fyers login URL: {e}", 500


@app.route("/fyers/callback")
def fyers_callback():
    """Fyers OAuth Redirect URI callback: captures authorization code, gets token, and caches it."""
    auth_code = request.args.get("auth_code") or request.args.get("code")
    if not auth_code:
        return "Authorization code missing in Fyers callback parameters.", 400
        
    try:
        from fyers_auth import save_access_token
        save_access_token(auth_code)
        
        # Display elegant responsive confirmation page
        return """
        <!DOCTYPE html>
        <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>Fyers Authentication Success</title>
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                        background: #0d1117;
                        color: #c9d1d9;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100vh;
                        margin: 0;
                    }
                    .card {
                        background: #161b22;
                        border: 1px solid #30363d;
                        border-radius: 12px;
                        padding: 40px;
                        text-align: center;
                        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
                        max-width: 450px;
                    }
                    h1 { color: #58a6ff; font-size: 24px; margin-bottom: 16px; font-weight: 600; }
                    p { font-size: 15px; line-height: 1.6; margin-bottom: 28px; color: #8b949e; }
                    a {
                        background: #238636;
                        color: #ffffff;
                        padding: 12px 24px;
                        text-decoration: none;
                        border-radius: 6px;
                        font-weight: 600;
                        display: inline-block;
                        transition: background 0.2s;
                    }
                    a:hover { background: #2ea043; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Authentication Successful!</h1>
                    <p>The daily Fyers API access token has been generated and cached locally. The Elite Breakout scanners can now pull premium data without rate limits.</p>
                    <a href="/admin">Go to Dashboard</a>
                </div>
            </body>
        </html>
        """, 200
    except Exception as e:
        logger.exception(f"Fyers callback token exchange failed")
        return f"Error exchanging Fyers token: {e}", 500


@app.route("/admin/export/<table>")
@admin_required

def export_csv_data(table):
    """Exports the requested database table as a CSV file."""
    # Prevent SQL injection by strictly whitelisting allowed tables
    valid_tables = ["alerts", "scanner_health", "system_state", "ai_concall_cache_v3"]
    if table not in valid_tables:
        return jsonify({"error": "Invalid table requested."}), 400
        
    try:
        from database import get_connection
        import io
        import csv
        from flask import Response
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]
                
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(col_names)
                for row in rows:
                    writer.writerow(row)
                    
                csv_data = output.getvalue()
                
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={table}_export.csv"}
        )
    except Exception as e:
        logger.exception(f"Error exporting CSV for table {table}")
        return jsonify({"error": str(e)}), 500

@app.route("/admin/export/watchlist/<list_type>")
@admin_required
def export_watchlist(list_type):
    """Exports the daily generated watchlist CSVs."""
    from config import DATA_DIR
    import os
    from flask import send_file
    
    if list_type == "fundamental":
        file_path = os.path.join(DATA_DIR, "elite_fundamental_watchlist.parquet")
        filename = "elite_fundamental_watchlist.csv"
    elif list_type == "excluded":
        # The excluded file is still generated as CSV in daily_builder
        file_path = os.path.join(DATA_DIR, "elite_fundamental_watchlist_excluded.csv")
        filename = "elite_fundamental_watchlist_excluded.csv"
    else:
        return jsonify({"error": "Invalid list type requested."}), 400
        
    if not os.path.exists(file_path):
        return jsonify({"error": "Watchlist file not found. Ensure daily builder has run."}), 404
        
    if file_path.endswith('.parquet'):
        import pandas as pd
        import io
        from flask import Response
        try:
            df = pd.read_parquet(file_path)
            output = io.StringIO()
            df.to_csv(output, index=False)
            csv_data = output.getvalue()
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-disposition": f"attachment; filename={filename}"}
            )
        except Exception as e:
            logger.exception("Failed to convert parquet to CSV for export")
            return jsonify({"error": "Failed to convert file for export"}), 500
            
    return send_file(file_path, as_attachment=True, download_name=filename)


@app.route("/api/summary")
@login_required
def api_summary():
    """Quick JSON summary — useful for curl checks, loaded from DB."""
    try:
        from database import get_system_state
        val = get_system_state("performance_data")
        if val:
            data = json.loads(val)
            summary = data.get("summary", {})
            from database import get_ai_cache_count
            summary["ai_cache_count"] = get_ai_cache_count()
            return jsonify(summary)
    except Exception:
        logger.exception("❌ /api/summary failed")
    return jsonify({"error": "No data yet"}), 404


@app.route("/api/shortlist")
@login_required
def api_shortlist():
    """Returns the elite fundamental watchlist data as JSON."""
    from config import WATCHLIST_PATH
    import pandas as pd
    try:
        if not os.path.exists(WATCHLIST_PATH):
            return jsonify([])
        df = pd.read_parquet(WATCHLIST_PATH)
        import json
        records = json.loads(df.to_json(orient="records"))
        return jsonify(records)
    except Exception as e:
        logger.exception(f"Failed to load shortlist JSON")
        return jsonify([])

@app.route("/api/shortlist_excluded")
@login_required
def api_shortlist_excluded():
    """Returns excluded stocks data as JSON."""
    from config import DATA_DIR
    import pandas as pd
    try:
        excluded_path = os.path.join(DATA_DIR, "elite_fundamental_watchlist_excluded.csv")
        if not os.path.exists(excluded_path):
            return jsonify([])
        df = pd.read_csv(excluded_path).fillna("")
        import json
        records = json.loads(df.to_json(orient="records"))
        return jsonify(records)
    except Exception as e:
        logger.exception(f"Failed to load excluded stocks JSON")
        return jsonify([])

@app.route("/api/wealth")
@login_required
def api_wealth():
    """Returns the elite wealth system data as JSON."""
    from config import DATA_DIR
    import pandas as pd
    try:
        WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
        if not os.path.exists(WEALTH_PATH):
            return jsonify([])
        df = pd.read_parquet(WEALTH_PATH)
        import json
        from datetime import datetime
        records = json.loads(df.to_json(orient="records"))
        mtime = os.path.getmtime(WEALTH_PATH)
        generated_at = datetime.fromtimestamp(mtime).isoformat()
        return jsonify({"data": records, "generated_at": generated_at})
    except Exception as e:
        logger.exception(f"Failed to load wealth JSON")
        return jsonify([])

@app.route("/api/macro_state")
@login_required
def api_macro_state():
    """Returns the current Macro Regime state (Nifty correction)."""
    try:
        from wealth_engine import fetch_nifty_macro_state
        ret_6m, dist_52w = fetch_nifty_macro_state()
        r_6m = round(float(ret_6m), 2) if ret_6m is not None else None
        d_52w = round(float(dist_52w), 2) if dist_52w is not None else None
        return jsonify({
            "nifty_6m_return": r_6m,
            "nifty_dist_52w": d_52w,
            "bear_market_gate": bool(d_52w > 15.0) if d_52w is not None else False
        })
    except Exception as e:
        logger.exception(f"Failed to fetch macro state")
        return jsonify({"nifty_6m_return": 0, "nifty_dist_52w": 0, "bear_market_gate": False})


# ── Fetch errors API (admin) ─────────────────────────────────────────────────────
@app.route("/api/fetch_errors")
@login_required
def api_fetch_errors():
    """Return recent aggregated fetch errors for admin triage."""
    try:
        from database import get_all_fetch_errors
        rows = get_all_fetch_errors(200)
        return jsonify(serialize_datetimes(rows))
    except Exception:
        logger.exception("❌ /api/fetch_errors failed")
        return jsonify([]), 200

@app.route("/api/system_logs", methods=["GET"])
@login_required
def api_system_logs():
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT 
                        MIN(id) as id,
                        level, 
                        module, 
                        message, 
                        MAX(traceback) as traceback, 
                        COUNT(*) as occurrences,
                        MIN(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') as first_seen,
                        MAX(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') as last_seen
                    FROM system_logs
                    WHERE is_acknowledged = FALSE
                    GROUP BY level, module, message
                    ORDER BY last_seen DESC
                    LIMIT 100
                """)
                logs = cur.fetchall()
                # Format datetime to string
                for log in logs:
                    if log['first_seen']:
                        log['first_seen'] = log['first_seen'].strftime('%Y-%m-%d %I:%M:%S %p')
                    if log['last_seen']:
                        log['last_seen'] = log['last_seen'].strftime('%Y-%m-%d %I:%M:%S %p')
        return jsonify(logs), 200
    except Exception as e:
        logger.exception("Failed to fetch system logs")
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route("/api/system_logs/acknowledge", methods=["POST"])
@login_required
def acknowledge_system_log():
    try:
        data = request.json or {}
        message = data.get('message')
        module = data.get('module')
        
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE system_logs SET is_acknowledged = TRUE WHERE message = %s AND module = %s", (message, module))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception(f"Failed to acknowledge system log")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/system_logs/clear_all", methods=["POST"])
@login_required
def clear_all_system_logs():
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE system_logs SET is_acknowledged = TRUE WHERE is_acknowledged = FALSE")
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception("Failed to clear all system logs")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/fetch_errors/by_scanner", methods=["GET"])
@login_required
def api_fetch_errors_by_scanner():
    """Return unacknowledged fetch_errors for a specific scanner."""
    try:
        from database import get_fetch_errors_for_scanner
        scanner_name = request.args.get('name')
        if not scanner_name:
            return jsonify({"error": "Missing 'name' parameter"}), 400
        rows = get_fetch_errors_for_scanner(scanner_name)
        return jsonify(serialize_datetimes(rows))
    except Exception:
        logger.exception("❌ /api/fetch_errors/by_scanner failed")
        return jsonify([]), 200


@app.route("/api/fetch_errors/ack/<int:error_id>", methods=["POST"])
@login_required
def api_ack_fetch_error(error_id):
    """Acknowledge a specific fetch error so it stops alerting in UI."""
    try:
        from database import acknowledge_fetch_error
        ok = acknowledge_fetch_error(error_id)
        return jsonify({"ok": ok})
    except Exception:
        logger.exception("❌ /api/fetch_errors/ack failed")
        return jsonify({"ok": False}), 500


@app.route("/api/fetch_errors/all", methods=["DELETE"])
@login_required
def api_clear_all_fetch_errors():
    """Clear all fetch errors at once (acknowledge all)."""
    try:
        from database import acknowledge_all_fetch_errors
        ok = acknowledge_all_fetch_errors()
        return jsonify({"ok": ok})
    except Exception:
        logger.exception("❌ /api/fetch_errors/all DELETE failed")
        return jsonify({"ok": False}), 500


@app.route("/api/deposit_funds", methods=["POST"])
@login_required
def api_deposit_funds():
    """Deposit funds to capital_history (admin only)."""
    try:
        from database import deposit_funds, get_capital_info
        data = request.json or {}
        amount = float(data.get('amount', 0))
        
        if amount <= 0:
            return jsonify({"error": "Amount must be > 0"}), 400
        
        deposit_funds(amount)
        capital_info = get_capital_info()
        return jsonify({"ok": True, **capital_info})
    except Exception as e:
        logger.exception(f"❌ /api/deposit_funds failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/capital_info", methods=["GET"])
@login_required
def api_capital_info():
    """Get capital breakdown: base_capital, total_deposited, total_capital."""
    try:
        from database import get_capital_info
        info = get_capital_info()
        return jsonify(info)
    except Exception:
        logger.exception("❌ /api/capital_info failed")
        return jsonify({"base_capital": 0, "total_deposited": 0, "total_capital": 0})

@app.route("/api/sector_momentum", methods=["GET"])
@login_required
def api_sector_momentum():
    """Get sector momentum for the last 7 days."""
    try:
        from database import get_sector_momentum
        days = request.args.get('days', 7, type=int)
        data = get_sector_momentum(days)
        return jsonify(data)
    except Exception as e:
        logger.exception("❌ /api/sector_momentum failed")
        return jsonify([])


# ── MANUAL PORTFOLIO TRACKER ──────────────────────────────────────────────────
@app.route("/api/portfolio", methods=["GET"])
@login_required
def api_get_portfolio():
    """Returns manual portfolio with live recommendations based on Wealth Engine data."""
    try:
        from database import get_manual_portfolio
        from config import DATA_DIR
        import pandas as pd
        import os
        
        portfolio = get_manual_portfolio()
        if not portfolio:
            return jsonify([])

        # Load live wealth data to enrich the portfolio
        wealth_data = {}
        WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
        if os.path.exists(WEALTH_PATH):
            df = pd.read_parquet(WEALTH_PATH)
            # Create a lookup dictionary by Stock symbol
            for _, row in df.iterrows():
                wealth_data[row["Stock"]] = row.to_dict()

        def safe_num(v):
            if v is None: return 0.0
            try:
                f = float(v)
                import math
                return 0.0 if math.isnan(f) else f
            except (ValueError, TypeError):
                return 0.0

        enriched = []
        for p in portfolio:
            sym = p["symbol"]
            entry_price = safe_num(p["entry_price"])
            
            # Defaults
            live_data = wealth_data.get(sym, {})
            cmp = safe_num(live_data.get("cmp"))
            fm_score = safe_num(live_data.get("FM_Score"))
            signal = live_data.get("Signal") or ""
            ai_conf = safe_num(live_data.get("AI_Confidence"))
            category = live_data.get("Category") or ""

            pnl_pct = 0.0
            if cmp > 0 and entry_price > 0:
                pnl_pct = ((cmp - entry_price) / entry_price) * 100

            # Recommendation Engine Logic
            rec = "HOLD"
            if cmp == 0:
                rec = "NO DATA"
            elif fm_score > 0 and fm_score < 65:
                rec = "EXIT"
            elif signal and "SELL" in str(signal).upper():
                rec = "EXIT"
            elif fm_score >= 80 and cmp > 0 and pnl_pct <= -8:
                rec = "AVERAGE"
            
            p.update({
                "cmp": cmp,
                "pnl_pct": pnl_pct,
                "FM_Score": fm_score,
                "Signal": signal,
                "AI_Confidence": ai_conf,
                "Category": category,
                "Recommendation": rec,
                "Bucket": live_data.get("Portfolio_Bucket", "")
            })
            enriched.append(p)
            
        return jsonify(enriched)
    except Exception as e:
        logger.exception(f"Failed to get manual portfolio")
        return jsonify([])

@app.route("/api/portfolio/add", methods=["POST"])
@login_required
def api_add_portfolio():
    try:
        data = request.json
        symbol = data.get("symbol")
        entry_date = data.get("entry_date")
        entry_price = float(data.get("entry_price"))
        quantity = int(data.get("quantity"))
        
        if not symbol or not entry_date or not entry_price:
            return jsonify({"error": "Missing required fields"}), 400
            
        from database import add_portfolio_entry
        add_portfolio_entry(symbol, entry_date, entry_price, quantity)
        return jsonify({"success": True})
    except Exception as e:
        logger.exception(f"Failed to add portfolio entry")
        return jsonify({"error": str(e)}), 500

@app.route("/api/portfolio/remove", methods=["POST"])
@login_required
def api_remove_portfolio():
    try:
        data = request.json
        entry_id = int(data.get("id"))
        from database import remove_portfolio_entry
        remove_portfolio_entry(entry_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.exception(f"Failed to remove portfolio entry")
        return jsonify({"error": str(e)}), 500

@app.route("/api/data_fetch_health")
@login_required
def api_data_fetch_health():
    """Return the health status of external data providers (cache/fetch failures)."""
    try:
        from database import get_all_data_fetch_health
        rows = get_all_data_fetch_health()
        
        # Inject Fyers API session health if using Fyers data provider
        from config import DATA_PROVIDER
        if DATA_PROVIDER == "fyers":
            from fyers_auth import get_access_token
            token = get_access_token()
            token_valid = token is not None
            
            fyers_row = {
                "source_name": "Fyers API Session",
                "last_success": datetime.now(IST) if token_valid else None,
                "last_failure": None if token_valid else datetime.now(IST),
                "consecutive_failures": 0 if token_valid else 1,
                "error_msg": "Session active and token cached." if token_valid else 'Token missing or expired. <a href="/fyers/login" style="color:#00d4a1; font-weight:bold; text-decoration:underline;">Click here to Authorize Fyers API</a>.',
                "is_acknowledged": 0,
                "updated_at": datetime.now(IST)
            }
            rows.append(fyers_row)
            
        return jsonify(serialize_datetimes(rows))
    except Exception:
        logger.exception("❌ /api/data_fetch_health failed")
        return jsonify([]), 500



@app.route('/api/todays_alerts')
@login_required
def api_todays_alerts():
    """Return alerts fired today (includes seen flags)."""
    try:
        from database import get_todays_alerts
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')
        rows = get_todays_alerts(today)
        return jsonify(serialize_datetimes(rows))
    except Exception:
        logger.exception('❌ /api/todays_alerts failed')
        return jsonify([]), 200


@app.route('/api/alert/mark_seen', methods=['POST'])
@login_required
def api_mark_alert_seen():
    """Mark an alert as seen by user/admin via POST {id: int, role: 'user'|'admin'}."""
    try:
        data = request.json or {}
        alert_id = int(data.get('id'))
        role = data.get('role', 'user')
        from database import mark_alert_seen
        ok = mark_alert_seen(alert_id, role)
        return jsonify({'success': bool(ok)})
    except Exception as e:
        logger.exception('❌ /api/alert/mark_seen failed')
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert/reject', methods=['POST'])
@login_required
def api_reject_alert():
    try:
        data = request.json or {}
        alert_id = int(data.get('id'))
        from database import reject_alert
        ok = reject_alert(alert_id)
        if ok:
            import threading
            from performance_tracker import build_performance_data
            threading.Thread(target=build_performance_data).start()
        return jsonify({'success': bool(ok)})
    except Exception as e:
        logger.exception('❌ /api/alert/reject failed')
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert/reject_multiple', methods=['POST'])
@login_required
def api_reject_multiple_alerts():
    try:
        data = request.json or {}
        # Support payloads like { "ids": [1,2,3] } or comma-separated string
        ids = data.get('ids') or data.get('alert_ids') or []
        if isinstance(ids, str):
            ids = [int(x) for x in ids.split(',') if x.strip()]
        else:
            ids = [int(x) for x in ids] if ids else []

        from database import reject_multiple_alerts
        ok = reject_multiple_alerts(ids)
        if ok:
            import threading
            from performance_tracker import build_performance_data
            threading.Thread(target=build_performance_data).start()
        return jsonify({'success': bool(ok)})
    except Exception as e:
        logger.exception('❌ /api/alert/reject_multiple failed')
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert/accept', methods=['POST'])
@login_required
def api_accept_alert():
    try:
        data = request.json or {}
        alert_id = int(data.get('id'))
        from database import accept_alert
        ok = accept_alert(alert_id)
        if ok:
            import threading
            from performance_tracker import build_performance_data
            threading.Thread(target=build_performance_data).start()
        return jsonify({'success': bool(ok)})
    except Exception as e:
        logger.exception('❌ /api/alert/accept failed')
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert/reallocate', methods=['POST'])
@login_required
def api_reallocate_alert():
    try:
        data = request.json or {}
        alert_id = int(data.get('id'))
        from database import reallocate_capital
        ok = reallocate_capital(alert_id)
        if ok:
            import threading
            from performance_tracker import build_performance_data
            threading.Thread(target=build_performance_data).start()
            
            from database import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT capital_allocated, shares_bought, stop_loss, target_price FROM alerts WHERE id = %s", (alert_id,))
                    row = cur.fetchone()
                    if row:
                        return jsonify({
                            'success': True,
                            'capital_allocated': float(row[0] or 0),
                            'shares_bought': int(row[1] or 0),
                            'stop_loss': float(row[2] or 0),
                            'target_price': float(row[3] or 0)
                        })
        return jsonify({'success': bool(ok)})
    except Exception as e:
        logger.exception('❌ /api/alert/reallocate failed')
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert/reallocate_multiple', methods=['POST'])
@login_required
def api_reallocate_multiple_alerts():
    try:
        data = request.json or {}
        ids = data.get('ids') or data.get('alert_ids') or []
        if isinstance(ids, str):
            ids = [int(x) for x in ids.split(',') if x.strip()]
        else:
            ids = [int(x) for x in ids] if ids else []
            
        from database import reallocate_capital_multiple
        results = reallocate_capital_multiple(ids)
        if results and len(results) > 0:
            import threading
            from performance_tracker import build_performance_data
            threading.Thread(target=build_performance_data).start()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        logger.exception('❌ /api/alert/reallocate_multiple failed')
        return jsonify({'error': str(e)}), 500


@app.route("/api/data_fetch_health/acknowledge/<source_name>", methods=["POST"])
@login_required
def api_acknowledge_health(source_name):
    """Admin endpoint to dismiss persistent API warnings."""
    try:
        from database import acknowledge_data_fetch_health
        acknowledge_data_fetch_health(source_name)
        return jsonify({"status": "success", "source": source_name})
    except Exception as e:
        logger.exception(f"❌ /api/data_fetch_health/acknowledge failed for {source_name}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/scanner_health/acknowledge/<scanner_name>", methods=["POST"])
@login_required
def api_acknowledge_scanner_health(scanner_name):
    """Admin endpoint to dismiss persistent scanner warnings."""
    try:
        from database import acknowledge_scanner_health
        acknowledge_scanner_health(scanner_name)
        return jsonify({"status": "success", "scanner": scanner_name})
    except Exception as e:
        logger.exception(f"❌ /api/scanner_health/acknowledge failed for {scanner_name}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/trigger_scanner/<scanner_name>", methods=["POST"])
@admin_required
def api_trigger_scanner(scanner_name):
    """Admin endpoint to manually trigger any scanner regardless of market hours.
    Runs the scanner in a background thread and returns immediately.
    """
    try:
        force_refresh = request.args.get('force_refresh', 'false') == 'true'
        if scanner_name == 'MULTIBAGGER' and force_refresh:
            import os
            from config import DATA_DIR
            cache_path = os.path.join(DATA_DIR, "multibagger_fundamentals_cache.json")
            if os.path.exists(cache_path):
                os.remove(cache_path)
                logger.info(f"🗑️ Cleared Multibagger fundamentals cache at {cache_path} before manual trigger.")
                
            # One-off data migration: Fix any corrupted current_score values > 100
            try:
                from database import get_connection
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE wealth_buy_alert SET current_score = fm_score WHERE current_score > 100;")
                        conn.commit()
                logger.info("🔧 Fixed corrupted current_score values > 100 in the database.")
            except Exception as e:
                logger.error(f"Failed to fix corrupted current_score values: {e}")
                
        from main import trigger_scanner_manual
        result = trigger_scanner_manual(scanner_name)
        status_code = 200 if result["status"] == "ok" else 400
        return jsonify(result), status_code
    except Exception as e:
        logger.exception(f"❌ /api/admin/trigger_scanner failed for {scanner_name}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/wealth")
@login_required
def route_wealth():
    from config import BASE_DIR
    return send_file(os.path.join(BASE_DIR, "app", "wealth_dashboard.html"))

@app.route("/api/download_shortlist")
@login_required
def api_download_shortlist():
    """Serves the elite fundamental watchlist as a CSV file."""
    from config import WATCHLIST_PATH
    import pandas as pd
    try:
        if not os.path.exists(WATCHLIST_PATH):
            return "No watchlist generated yet", 404
            
        csv_path = WATCHLIST_PATH.replace(".parquet", ".csv")
        df = pd.read_parquet(WATCHLIST_PATH)
        df.to_csv(csv_path, index=False)
        
        return send_file(
            csv_path,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"Elite_Watchlist_{datetime.now(IST).strftime('%Y%m%d')}.csv"
        )
    except Exception as e:
        logger.exception(f"Failed to generate shortlist CSV")
        return "Server Error", 500

@app.route("/api/scanner_status")
@login_required
def api_scanner_status():
    """
    Return per-scanner health stats and today's trades — all sourced from Postgres.
    scanner_health table holds status/last_success/error.
    alerts table is queried live for today's trades per scanner.
    """
    try:
        import os
        from database import get_all_scanner_health, get_scanner_today_trades
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today_str = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')

        health_rows = get_all_scanner_health()
        result = {}
        for row in health_rows:
            sc = row["scanner_name"]
            today_trades = get_scanner_today_trades(sc, today_str)
            
            # Special case for Wealth Engine: It doesn't write to the alerts table.
            # We must parse its parquet file to get today's trades for the tooltip to work!
            if sc == "Wealth Engine":
                try:
                    import os, pandas as pd
                    from config import DATA_DIR
                    wealth_path = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
                    if os.path.exists(wealth_path):
                        wdf = pd.read_parquet(wealth_path)
                        # Filter for BUY signals
                        buy_df = wdf[wdf["Signal_Code"] == "BUY"]
                        today_trades = []
                        for _, wrow in buy_df.iterrows():
                            today_trades.append({
                                "symbol": wrow.get("Stock", ""),
                                "category": wrow.get("Portfolio_Bucket", ""),
                                "signals": wrow.get("Signal", ""),
                                "entry_price": wrow.get("cmp", 0),
                                "alert_time": today_str,
                                "stop_loss": None,
                                "target_price": None,
                                "exit_price": None,
                                "closed_at": None,
                                "pnl_pct": None,
                                "status": "OPEN",
                                "score": wrow.get("FM_Score", 0)
                            })
                except Exception as e:
                    pass

            # Enrich AI/Pledge workers with progress metrics
            # Enrich AI/Pledge workers with progress metrics
            extra = {}
            try:
                if sc in ("AI Worker", "Pledge Worker"):
                    # Compute total watchlist size (included + excluded)
                    import pandas as pd
                    total_needed = 0
                    from config import DATA_DIR
                    for f in [
                        os.path.join(DATA_DIR, 'elite_fundamental_watchlist.csv'),
                        os.path.join(DATA_DIR, 'elite_fundamental_watchlist_excluded.csv'),
                    ]:
                        try:
                            if os.path.exists(f):
                                dfw = pd.read_csv(f)
                                if 'Stock' in dfw.columns:
                                    total_needed += dfw['Stock'].dropna().shape[0]
                        except Exception:
                            pass
                    from database import get_ai_concall_stats, get_promoter_pledge_stats
                    if sc == 'AI Worker':
                        stats = get_ai_concall_stats()
                    else:
                        stats = get_promoter_pledge_stats()
                    extra = {
                        'progress': stats.get('total_cached', 0),
                        'total_needed': total_needed,
                        'last_processed_symbol': stats.get('last_symbol'),
                        'last_processed_at': stats.get('last_updated')
                    }
            except Exception:
                logger.exception('Failed to compute worker progress metrics')
    
            result[sc] = {
                    "status":        row["status"],
                    "last_success":  row["last_success"],
                    "today_alerts":  row["today_alerts"],
                    "error":         row["error_msg"],
                    "updated_at":    row["updated_at"],
                    "is_acknowledged": row["is_acknowledged"],
                    "processed_count": row.get("processed_count"),
                    "total_count":   row.get("total_count"),
                    "scheduled_for": row.get("scheduled_for"),
                    "today_trades":  [
                        {
                            "symbol":       t["symbol"],
                            "category":     t["category"] or "",
                            "signals":      t["signals"] or "",
                            "entry_price":  float(t["entry_price"]) if t["entry_price"] else None,
                            "entry_time":   t["alert_time"] or "",
                            "stop_loss":    float(t["stop_loss"]) if t["stop_loss"] else None,
                            "target_price": float(t["target_price"]) if t["target_price"] else None,
                            "exit_price":   float(t["exit_price"]) if t["exit_price"] else None,
                            "closed_at":    t["closed_at"],
                            "pnl_pct":      float(t["pnl_pct"]) if t["pnl_pct"] is not None else None,
                            "status":       t["status"] or "OPEN",
                            "score":        t["score"],
                        }
                        for t in today_trades
                    ],
                }
            # Merge extras if present
            if extra:
                result[sc].update(extra)
        return jsonify(serialize_datetimes(result))
    except Exception as exc:
        logger.exception("❌ /api/scanner_status failed")
        return jsonify({}), 200

# ── Endpoints for Market Ticker & Catalyst News ────────────────────────────────────

_indices_cache = {"data": None, "timestamp": 0}
_indices_lock = threading.Lock()

@app.route("/api/indices")
@login_required
def api_indices():
    """Fetch live NIFTY 50, BANKNIFTY, and SENSEX with 1-min caching."""
    with _indices_lock:
        if _indices_cache["data"] and (time.time() - _indices_cache["timestamp"] < 60):
            return jsonify(_indices_cache["data"])
        
    try:
        symbols = {"NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
        data = {}
        from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError
        for name, sym in symbols.items():
            try:
                yf_acquire(context=f"DashboardServer.refresh_valuation | {sym}")
                try:
                    ticker = yf.Ticker(sym)
                    info = ticker.info
                finally:
                    yf_release()
            except CircuitOpenError as ce:
                logger.error(f"YFinance circuit open; abort indices fetch: {ce}")
                return jsonify(_indices_cache["data"] or {})
            except Exception as e:
                msg = str(e).lower()
                if 'too many requests' in msg or 'rate limit' in msg:
                    record_rate_limit(context=f"DashboardServer.refresh_valuation | {sym}")
                logger.warning(f"Failed to fetch index {sym}: {e}")
                info = {}

            price = info.get("regularMarketPrice") or info.get("previousClose")
            prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
            pct_change = 0.0
            if price and prev_close:
                pct_change = round(((price - prev_close) / prev_close) * 100, 2)
            data[name] = {"price": price, "pct_change": pct_change}
            
        with _indices_lock:
            _indices_cache["data"] = data
            _indices_cache["timestamp"] = time.time()
        try:
            mark_success('yfinance')
        except Exception:
            logger.exception('Failed to report yfinance success from dashboard indices')
        return jsonify(data)
    except Exception as e:
        logger.warning(f"Failed to fetch indices from yfinance: {e}")
        try:
            mark_failure('yfinance', f"{e} (Dashboard Indices)")
        except Exception:
            logger.exception('Failed to report yfinance failure from dashboard indices')
        return jsonify(_indices_cache["data"] or {})

_news_cache = {}
_news_lock = threading.Lock()

@app.route("/api/news/<symbol>")
@login_required
def api_news(symbol):
    """Fetch recent 3 news headlines for a symbol with 15-min caching."""
    # Append .NS for Yahoo Finance compatibility if not present and if it doesn't have an extension
    yf_symbol = symbol.replace('_', '-') if "." in symbol else f"{symbol.replace('_', '-')}.NS"
    
    with _news_lock:
        cached = _news_cache.get(yf_symbol)
        if cached and (time.time() - cached["timestamp"] < 900): # 15 min cache
            return jsonify(cached["data"])
            
        try:
            try:
                from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError
                yf_acquire(context=f"DashboardServer.api_fundamental_details | {yf_symbol}")
                try:
                    ticker = yf.Ticker(yf_symbol)
                    raw_news = ticker.news[:3]
                finally:
                    yf_release()
            except CircuitOpenError as ce:
                logger.error(f"YFinance circuit open; abort news fetch for {yf_symbol}: {ce}")
                return jsonify([])
            except Exception as e:
                msg = str(e).lower()
                if 'too many requests' in msg or 'rate limit' in msg:
                    from yf_rate_limiter import record_rate_limit
                    record_rate_limit(context=f"DashboardServer.api_fundamental_details | {yf_symbol}")
                logger.exception(f"Failed to fetch news for {yf_symbol}")
                try:
                    mark_failure('yfinance', f"{e} (Dashboard News: {yf_symbol})")
                except Exception:
                    logger.exception('Failed to report yfinance failure from dashboard news')
                return jsonify([])

            news = []
            for item in raw_news:
                n = item.get("content", item)
                news.append({
                    "title": n.get("title", ""),
                    "summary": n.get("summary", ""),
                    "link": n.get("link") or n.get("clickThroughUrl", {}).get("url", "") or n.get("canonicalUrl", {}).get("url", ""),
                    "provider": n.get("provider", {}).get("displayName", ""),
                    "date": n.get("pubDate", "") or n.get("providerPublishTime", "")
                })

            with _news_lock:
                _news_cache[yf_symbol] = {"data": news, "timestamp": time.time()}
            try:
                mark_success('yfinance')
            except Exception:
                logger.exception('Failed to report yfinance success from dashboard news')
            return jsonify(news)
        except Exception as e:
            msg = str(e).lower()
            if 'too many requests' in msg or 'rate limit' in msg:
                from yf_rate_limiter import record_rate_limit
                record_rate_limit()
            logger.exception(f"Failed to fetch news for {yf_symbol}")
            try:
                mark_failure('yfinance', f"{e} (Dashboard News: {yf_symbol})")
            except Exception:
                logger.exception('Failed to report yfinance failure from dashboard news')
            return jsonify([])

import subprocess
import json

@app.route("/api/notices/<symbol>")
@login_required
def api_notices(symbol):
    """Fetch recent corporate announcements from NSE via requests.Session to bypass WAF."""
    yf_symbol = symbol.replace('.NS', '').replace('_', '-')
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={yf_symbol}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*'
    }
    
    try:
        try:
            from curl_cffi import requests as cffi_requests
            s = cffi_requests.Session(impersonate="chrome110")
        except ImportError:
            import requests
            s = requests.Session()
        # Ping homepage to establish cookies
        s.get('https://www.nseindia.com', headers=headers, timeout=5)
        # Fetch the actual data
        r = s.get(url, headers=headers, timeout=5)
        
        if r.status_code != 200:
            logger.error(f"NSE API returned {r.status_code} for {symbol}")
            try:
                mark_failure('nse_announcements', f'status_code={r.status_code}')
            except Exception:
                logger.exception('Failed to report nse_announcements failure')
            return jsonify([])
            
        data = r.json()
        notices = []
        for n in data[:4]:
            desc = str(n.get("desc", ""))
            # Truncate overly long descriptions
            if len(desc) > 40:
                desc = desc[:37] + "..."
                
            notices.append({
                "date": n.get("an_dt", "").split(" ")[0],
                "desc": desc,
                "link": n.get("attchmntFile", "")
            })
        try:
            mark_success('nse_announcements')
        except Exception:
            logger.exception('Failed to report nse_announcements success')
        return jsonify(notices)
    except Exception as e:
        logger.exception(f"Failed to fetch notices for {symbol}")
        try:
            mark_failure('nse_announcements', e)
        except Exception:
            logger.exception('Failed to report nse_announcements exception')
        return jsonify([])

@app.route('/api/all_tickers', methods=['GET'])
@login_required
def api_all_tickers():
    """Returns a list of all active NSE symbols for frontend autocomplete."""
    try:
        import pandas as pd
        import os
        tickers = set()
        for f in ['data/elite_fundamental_watchlist.csv', 'data/elite_fundamental_watchlist_excluded.csv']:
            if os.path.exists(f):
                try:
                    df = pd.read_csv(f)
                    if 'Stock' in df.columns:
                        tickers.update(df['Stock'].dropna().unique().tolist())
                except Exception: pass
        if tickers:
            return jsonify(sorted(list(tickers)))
        return jsonify([])
    except Exception as e:
        logger.exception(f"Failed to fetch tickers")
        return jsonify([])

def fetch_and_analyze_concall(symbol):
    """
    Internal function to fetch and analyze concall, returning a dict instead of a Response.
    
    EXPERIMENTAL AI SENTIMENT SIGNAL:
    - This function uses an LLM (Claude-3.5-Sonnet / Gemini-1.5-Pro / GPT-4o) to analyze 
      the latest management concall transcripts fetched via the NSE/BSE corporate announcements API.
    - It is explicitly experimental and NOT backtested, as historical point-in-time transcripts
      are not systematically available in our backtest universe.
    - The `AI_Confidence` score is a heuristic 1-10 scale generated by prompt-based analysis 
      of management tone regarding guidance, margin expansion, and order book visibility. 
      It is NOT a statistically calibrated probability distribution.
    - In the live Wealth Engine scoring model (`wealth_engine.py`), this signal contributes 
      a maximum of ±5 points (which is only 5% of the total 100-point rubric).
    - Can be bypassed entirely in `config.py` via `ENABLE_AI_SENTIMENT_SCORE = False`.
    """
    yf_symbol = symbol.replace('.NS', '')
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={yf_symbol}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*'
    }
    
    try:
        try:
            from curl_cffi import requests as cffi_requests
            s = cffi_requests.Session(impersonate="chrome110")
        except ImportError:
            import requests
            s = requests.Session()
        s.get('https://www.nseindia.com', headers=headers, timeout=5)
        r = s.get(url, headers=headers, timeout=5)
        
        if r.status_code != 200:
            return {"error": "Failed to fetch NSE announcements."}
            
        data = r.json()
        target_pdfs = []
        
        # Priority 1: Transcripts
        for n in data:
            desc = str(n.get("desc", "")).lower()
            if "transcript" in desc:
                url = str(n.get("attchmntFile", ""))
                if url.lower().endswith(".pdf") and url not in target_pdfs:
                    target_pdfs.append(url)
            if len(target_pdfs) == 2: break
                
        # Priority 2: Earnings / Investor Presentations
        if not target_pdfs:
            for n in data:
                desc = str(n.get("desc", "")).lower()
                if "presentation" in desc or "earnings" in desc:
                    url = str(n.get("attchmntFile", ""))
                    if url.lower().endswith(".pdf") and url not in target_pdfs:
                        target_pdfs.append(url)
                if len(target_pdfs) == 2: break
                    
        # Priority 3: General Concall Updates (Might just be a schedule)
        if not target_pdfs:
            for n in data:
                desc = str(n.get("desc", "")).lower()
                if "con. call" in desc or "investor meet" in desc:
                    url = str(n.get("attchmntFile", ""))
                    if url.lower().endswith(".pdf") and url not in target_pdfs:
                        target_pdfs.append(url)
                if len(target_pdfs) == 2: break
                
        if not target_pdfs:
            return {"error": "No recent concall transcripts or investor presentations found on NSE."}
            
        target_pdf = target_pdfs[0]
        target_pdf_2 = target_pdfs[1] if len(target_pdfs) > 1 else None
            
        # Check Cache
        try:
            from database import get_cached_concall_analysis, save_concall_analysis
        except ImportError:
            from database import get_cached_concall_analysis, save_concall_analysis
            
        cached_data = get_cached_concall_analysis(symbol, target_pdf)
        if cached_data:
            logger.info(f"Returning CACHED AI analysis for {symbol}")
            return cached_data
            
        # Parse the PDF
        import sys
        if os.path.dirname(__file__) not in sys.path:
            sys.path.insert(0, os.path.dirname(__file__))
            
        try:
            from pdf_parser import extract_text_from_nse_pdf
        except ImportError:
            from pdf_parser import extract_text_from_nse_pdf
            
        text_1 = extract_text_from_nse_pdf(target_pdf)
        if not text_1:
            return {"error": "Could not extract text from the PDF document."}
            
        text = "--- LATEST QUARTER ---\n" + text_1
        
        if target_pdf_2:
            text_2 = extract_text_from_nse_pdf(target_pdf_2)
            if text_2:
                text += "\n\n--- PREVIOUS QUARTER ---\n" + text_2
            
        # Analyze with AI
        try:
            from ai_analyzer import analyze_concall_text
        except ImportError:
            from ai_analyzer import analyze_concall_text
            
        ai_data = analyze_concall_text(text)
        
        if "error" in ai_data:
            return ai_data
            
        # Save to Cache
        save_concall_analysis(symbol, target_pdf, ai_data)
        
        return ai_data
    except Exception as e:
        logger.exception(f"Error in concall AI analysis for {symbol}")
        return {"error": str(e)}

@app.route("/api/concall_ai/<symbol>")
@login_required
def api_concall_ai(symbol):
    from database import get_recent_concall_analysis
    cached = get_recent_concall_analysis(symbol, max_age_days=60)
    if cached:
        return jsonify(cached)
        
    res = fetch_and_analyze_concall(symbol)
    if "error" in res:
        return jsonify(res), 500 if "extract text" in res.get("error", "") else 404
    return jsonify(res)

# ── Multibagger Watchlist API ───────────────────────────────────────────────────────────

@app.route("/api/multibagger/watchlist", methods=["GET"])
@login_required
def get_multibagger_watchlist():
    """Returns all stockupdates.watchlist entries for the Multibagger Watchlist tab."""
    from database import get_connection
    from psycopg2.extras import RealDictCursor
    try:
        status_filter = request.args.get("status")
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status_filter:
                    cur.execute("""
                        SELECT symbol, buy_zone_low, buy_zone_high, latest_price,
                               growth_score, value_score, trend_score, total_score,
                               bucket, status, notes, last_alert_price, last_alert_at, last_updated

                        FROM stockupdates.watchlist
                        WHERE status = %s
                        ORDER BY total_score DESC NULLS LAST
                    """, (status_filter,))
                else:
                    cur.execute("""
                        SELECT symbol, buy_zone_low, buy_zone_high, latest_price,
                               growth_score, value_score, trend_score, total_score,
                               bucket, status, notes, last_alert_price, last_alert_at, last_updated
                        FROM stockupdates.watchlist
                        ORDER BY total_score DESC NULLS LAST
                    """)
                rows = cur.fetchall()
        # Convert Decimal/datetime to JSON-safe types
        import decimal, datetime as _dt
        def safe(row):
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, decimal.Decimal):
                    d[k] = float(v)
                elif isinstance(v, (_dt.datetime, _dt.date)):
                    d[k] = v.isoformat()
            return d
        return jsonify([safe(r) for r in rows])
    except Exception as e:
        logger.exception(f"Failed to fetch multibagger watchlist")
        return jsonify([])

# ── Wealth Buy Alerts API ──────────────────────────────────────────────────────────────

@app.route("/api/wealth/alerts", methods=["GET"])
@login_required
def get_wealth_alerts():
    """Retrieve wealth buy alerts (all or filtered by symbol)."""
    from database import get_wealth_buy_alerts, get_today_wealth_alerts
    try:
        symbol = request.args.get("symbol")
        today_only = request.args.get("today", "").lower() == "true"
        
        if today_only:
            alerts = get_today_wealth_alerts()
        elif symbol:
            alerts = get_wealth_buy_alerts(symbol=symbol)
        else:
            alerts = get_wealth_buy_alerts()
        
        return jsonify(alerts)
    except Exception as e:
        logger.exception(f"❌ Error fetching wealth alerts")
        return jsonify({"error": str(e)}), 500


@app.route("/api/wealth/save-alert", methods=["POST"])
@admin_required
def save_wealth_alert():
    """Save a new wealth buy alert."""
    from database import save_wealth_buy_alert
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "").upper()
        alert_price = data.get("alert_price")
        breakout_type = data.get("breakout_type")
        fm_score = data.get("fm_score")
        notes = data.get("notes")
        
        if not symbol or alert_price is None:
            return jsonify({"error": "Symbol and alert_price are required"}), 400
            
        try:
            alert_price = float(alert_price)
            if alert_price <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "alert_price must be a positive number"}), 400
            
        if not breakout_type or not isinstance(breakout_type, str) or not breakout_type.strip():
            return jsonify({"error": "Valid breakout_type is required"}), 400
        
        success = save_wealth_buy_alert(symbol, alert_price, breakout_type.strip(), fm_score, notes)
        if success:
            return jsonify({"success": True, "message": f"Alert saved for {symbol} @ ₹{alert_price}"})
        else:
            return jsonify({"error": "Failed to save alert"}), 500
    except Exception as e:
        logger.exception(f"❌ Error saving wealth alert")
        return jsonify({"error": str(e)}), 500


@app.route("/api/wealth/update-alert/<int:alert_id>", methods=["POST"])
@admin_required
def update_wealth_alert(alert_id):
    """Update status of a wealth buy alert."""
    from database import update_wealth_alert_status
    try:
        data = request.get_json() or {}
        status = data.get("status", "").upper()
        current_price = data.get("current_price")
        
        if status not in ["ACTIVE", "BUY", "SELL", "HOLD", "CLOSED"]:
            return jsonify({"error": "Invalid status"}), 400
         
        success = update_wealth_alert_status(alert_id, status, current_price)
        if success:
            return jsonify({"success": True, "message": f"Alert {alert_id} updated to {status}"})
        else:
            return jsonify({"error": "Failed to update alert"}), 500
    except Exception as e:
        logger.exception(f"❌ Error updating wealth alert")
        return jsonify({"error": str(e)}), 500


@app.route("/api/wealth/open-positions", methods=["GET"])
@login_required
def get_open_positions_api():
    """Get all open positions."""
    from database import get_open_positions
    try:
        positions = get_open_positions()
        return jsonify(positions)
    except Exception as e:
        logger.exception(f"❌ Error fetching open positions")
        return jsonify({"error": str(e)}), 500


@app.route("/api/wealth/closed-positions", methods=["GET"])
@login_required
def get_closed_positions_api():
    """Get closed positions (filterable by days)."""
    from database import get_closed_positions
    try:
        days = request.args.get("days", "30")
        days = int(days) if days.isdigit() else 30
        positions = get_closed_positions(days_back=days)
        return jsonify(positions)
    except Exception as e:
        logger.exception(f"❌ Error fetching closed positions")
        return jsonify({"error": str(e)}), 500


@app.route("/api/wealth/close-position", methods=["POST"])
@admin_required
def close_wealth_position():
    """Close an active wealth position."""
    from database import close_position
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "").upper()
        exit_price = data.get("exit_price")
        exit_signal = data.get("exit_signal")
        
        if not symbol or exit_price is None:
            return jsonify({"error": "Symbol and exit_price are required"}), 400
        
        success = close_position(symbol, exit_price, exit_signal)
        if success:
            return jsonify({"success": True, "message": f"Position closed for {symbol}"})
        else:
            return jsonify({"error": "No open position found"}), 404
    except Exception as e:
        logger.exception(f"❌ Error closing position")
        return jsonify({"error": str(e)}), 500

# ── Scanner DOWN helpers

# ── Scanner DOWN helpers — write to Postgres, not just memory ─────────────────────────

def notify_scanner_down(scanner_name: str, error: str) -> None:
    """Mark a scanner as DOWN in the DB. Called from watchdog on crash.
    
    For CRITICAL errors (not rate-limits or missing stock data), also:
    - Send a Telegram alert to admin
    - Insert an in-app notification visible on the admin dashboard
    """
    logger.warning(f"🔴 Scanner DOWN: {scanner_name} | {error}")
    try:
        from database import upsert_scanner_health, classify_error_severity, insert_notification
        upsert_scanner_health(scanner_name, status="DOWN", error_msg=error[:500])
        
        severity = classify_error_severity(error[:500])
        if severity == 'CRITICAL':
            # Telegram alert
            try:
                from telegram_engine import queue_telegram_message
                msg = (
                    f"🚨 <b>SCANNER DOWN</b>\n\n"
                    f"📛 <b>Scanner:</b> {scanner_name}\n"
                    f"❌ <b>Error:</b> {error[:300]}\n"
                    f"🕐 <b>Time:</b> {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%H:%M:%S IST')}"
                )
                queue_telegram_message(msg)
            except Exception:
                logger.exception(f"❌ Could not send Telegram alert for {scanner_name}")
            
            # In-app notification (visible on admin dashboard notification bell)
            try:
                insert_notification(
                    notif_type="scanner_down",
                    title=f"🚨 {scanner_name} is DOWN",
                    message=f"Error: {error[:400]}"
                )
            except Exception:
                logger.exception(f"❌ Could not insert notification for {scanner_name}")
    except Exception:
        logger.exception(f"❌ Could not persist DOWN status for {scanner_name}")



def clear_scanner_down(scanner_name: str) -> None:
    """Clear DOWN flag in DB when a scanner recovers / restarts."""
    logger.info(f"🟢 Scanner recovering: {scanner_name}")
    try:
        from database import upsert_scanner_health
        upsert_scanner_health(scanner_name, status="OK", error_msg=None)
    except Exception:
        logger.exception(f"❌ Could not clear DOWN status for {scanner_name}")


def start_dashboard_server():
    """Called from main.py in a daemon thread."""
    # Railway injects PORT automatically — never hardcode it.
    # If PORT is missing the default 8080 is used, but Railway will always set it.
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🌐 Dashboard server starting on port {port}")
    logger.info(f"🌐 Serving User HTML from: {USER_DASHBOARD_PATH or 'NOT FOUND'}")
    logger.info(f"🌐 Serving Admin HTML from: {ADMIN_DASHBOARD_PATH or 'NOT FOUND'}")
    logger.info(f"🌐 Performance JSON path: {PERF_JSON_PATH}")
    # use_reloader=False is critical — Flask reloader forks the process and
    # breaks Railway's single-process model and our threading setup.
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)



@app.route("/api/breakout_watchlist", methods=["GET"])
@login_required
def api_breakout_watchlist():
    """Returns the live multi-tf breakout watchlist from the database."""
    try:
        from database import get_active_breakout_watchlist
        data = get_active_breakout_watchlist()
        
        if data:
            try:
                import pandas as pd
                import os
                import yfinance as yf
                from concurrent.futures import ThreadPoolExecutor, as_completed
                from config import DATA_DIR
                
                symbols = list(set([d["symbol"] for d in data]))
                prices = {}

                def fetch_cmp(sym):
                    from datetime import datetime
                    import pytz
                    ist = pytz.timezone('Asia/Kolkata')
                    try:
                        yf_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
                        t = yf.Ticker(yf_sym)
                        price = float(t.fast_info.last_price)
                        if pd.isna(price):
                            raise ValueError("NaN price")
                        return sym, price, datetime.now(ist).isoformat()
                    except Exception as e:
                        # Fallback to local cache if live fetch fails
                        try:
                            sym_clean = sym.replace(':', '_')
                            latest_mtime = 0
                            best_file = None
                            for interval in ["1m", "5m", "15m", "30m", "1h", "1d"]:
                                file_path = os.path.join(DATA_DIR, "history", interval, f"{sym_clean}.parquet")
                                if os.path.exists(file_path):
                                    mtime = os.path.getmtime(file_path)
                                    if mtime > latest_mtime:
                                        latest_mtime = mtime
                                        best_file = file_path
                            if best_file:
                                df = pd.read_parquet(best_file)
                                if not df.empty and "Close" in df.columns:
                                    df_valid = df.dropna(subset=["Close"])
                                    if not df_valid.empty:
                                        dt_utc = datetime.utcfromtimestamp(latest_mtime).replace(tzinfo=pytz.utc)
                                        return sym, float(df_valid["Close"].iloc[-1]), dt_utc.astimezone(ist).isoformat()
                        except Exception:
                            pass
                        return sym, None, None

                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {executor.submit(fetch_cmp, sym): sym for sym in symbols}
                    for future in as_completed(futures):
                        sym, price, ts = future.result()
                        if price is not None:
                            prices[sym] = {"price": price, "ts": ts}
                            
                for d in data:
                    if d["symbol"] in prices:
                        d["cmp"] = prices[d["symbol"]]["price"]
                        d["last_updated"] = prices[d["symbol"]]["ts"]
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to fetch live CMP for watchlist: {e}")

        return jsonify({"status": "success", "data": serialize_datetimes(data)})
    except Exception as e:
        logger.exception("Failed to fetch breakout watchlist.")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/admin/pending_users", methods=["GET"])
@admin_required
def get_pending_users():
    try:
        with database.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, username, email, first_name, last_name, mobile, created_at FROM users WHERE is_active = FALSE AND (account_status = 'pending' OR account_status IS NULL)")
                rows = cur.fetchall()
                users = []
                for r in rows:
                    users.append({
                        "user_id": r[0],
                        "username": r[1],
                        "email": r[2],
                        "name": f"{r[3] or ''} {r[4] or ''}".strip(),
                        "mobile": r[5],
                        "created_at": r[6]
                    })
        return jsonify(users)
    except Exception as e:
        logger.exception(f"Failed to fetch pending users")
        return jsonify({"error": "Failed to fetch pending users"}), 500

@app.route("/admin/approve_user/<int:user_id>", methods=["POST"])
@admin_required
def approve_user(user_id):
    try:
        with database.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_active = TRUE, account_status = 'approved' WHERE user_id = %s", (user_id,))
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.exception(f"Failed to approve user")
        return jsonify({"error": "Failed to approve user"}), 500

@app.route("/admin/reject_user/<int:user_id>", methods=["POST"])
@admin_required
def reject_user(user_id):
    try:
        with database.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_active = FALSE, account_status = 'rejected', session_token = NULL WHERE user_id = %s", (user_id,))
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.exception(f"Failed to reject user")
        return jsonify({"error": "Failed to reject user"}), 500

@app.route("/admin/deactivate_user/<int:user_id>", methods=["POST"])
@admin_required
def deactivate_user(user_id):
    try:
        with database.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_active = FALSE, account_status = 'rejected', session_token = NULL WHERE user_id = %s", (user_id,))
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.exception(f"Failed to deactivate user")
        return jsonify({"error": "Failed to deactivate user"}), 500
