import os
import secrets
import sys

# Add app to path so we can import from database
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

try:
    from database import get_connection
    from werkzeug.security import generate_password_hash
except ImportError as e:
    print(f"Failed to import dependencies: {e}")
    sys.exit(1)

def force_reset_admin():
    try:
        password = secrets.token_urlsafe(16)
        p_hash = generate_password_hash(password, method='scrypt')
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Check if admin exists
                cur.execute("SELECT user_id FROM users WHERE username = 'admin'")
                row = cur.fetchone()
                
                if row:
                    cur.execute("UPDATE users SET password_hash = %s, is_active = TRUE, must_change_password = TRUE WHERE username = 'admin'", (p_hash,))
                    print("\n[SECURITY] Admin account updated.")
                else:
                    cur.execute("""
                        INSERT INTO users (username, email, mobile, password_hash, role, is_active, must_change_password)
                        VALUES ('admin', 'admin@elitebreakout.temp', '0000000000', %s, 'admin', TRUE, TRUE)
                    """, (p_hash,))
                    print("\n[SECURITY] Admin account created.")
            conn.commit()
            print(f"Login as 'admin' with password: {password}\n")
    except Exception as e:
        print(f"Failed to reset admin: {e}")

if __name__ == "__main__":
    print("Connecting to database...")
    force_reset_admin()
