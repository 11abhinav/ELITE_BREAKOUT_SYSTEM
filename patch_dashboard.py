import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'r') as f:
    code = f.read()

api_routes = """
# =====================================================================================
# NOTIFICATIONS API
# =====================================================================================
@app.route('/api/notifications', methods=['GET'])
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
                        n['created_at'] = n['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                return jsonify(notifications)
    except Exception as e:
        logger.error(f"Error fetching notifications: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/mark_seen/<int:notif_id>', methods=['POST'])
def mark_notification_seen(notif_id):
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE global_notifications SET is_seen = TRUE WHERE id = %s', (notif_id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error marking notification as seen: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/mark_all_seen', methods=['POST'])
def mark_all_notifications_seen():
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE global_notifications SET is_seen = TRUE WHERE is_seen = FALSE')
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error marking all notifications as seen: {e}")
        return jsonify({"error": str(e)}), 500

"""

if "def get_notifications():" not in code:
    code = code.replace("# =====================================================================================\n# APP INITIALIZATION & MAIN\n# =====================================================================================", api_routes + "# =====================================================================================\n# APP INITIALIZATION & MAIN\n# =====================================================================================")

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'w') as f:
    f.write(code)

print("dashboard_server.py patched.")
