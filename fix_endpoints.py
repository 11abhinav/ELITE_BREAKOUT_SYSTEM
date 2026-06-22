with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'r') as f:
    content = f.read()

# 1. Fix reject endpoint
old_reject = """@app.route('/api/alert/reject', methods=['POST'])
def api_reject_alert():
    try:
        data = request.json or {}
        alert_id = int(data.get('id'))
        from database import reject_alert
        ok = reject_alert(alert_id)
        return jsonify({'success': bool(ok)})"""

new_reject = """@app.route('/api/alert/reject', methods=['POST'])
def api_reject_alert():
    try:
        data = request.json or {}
        alert_id = int(data.get('id'))
        from database import reject_alert
        ok = reject_alert(alert_id)
        if ok:
            import threading
            from performance_tracker import build_performance_data
            threading.Thread(target=build_performance_data, daemon=True).start()
        return jsonify({'success': bool(ok)})"""

content = content.replace(old_reject, new_reject)

# 2. Fix shortlist_excluded
old_excluded = """        if not os.path.exists(excluded_path):
            return jsonify([])
        df = pd.read_csv(excluded_path)
        import json
        records = json.loads(df.to_json(orient="records"))
        return jsonify(records)"""

new_excluded = """        if not os.path.exists(excluded_path):
            return jsonify([])
        df = pd.read_csv(excluded_path).fillna("")
        import json
        records = json.loads(df.to_json(orient="records"))
        return jsonify(records)"""

content = content.replace(old_excluded, new_excluded)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'w') as f:
    f.write(content)
