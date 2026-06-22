import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/admin_dashboard.html', 'r') as f:
    content = f.read()

old_html = """    <div class="card" style="margin-bottom:28px; padding:16px;">
      <div style="max-height: 250px; overflow-y: auto;">
        <table class="data-table" id="signal-ladder-table" style="width:100%; text-align:left; font-size:12px;">
          <thead>
            <tr>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Symbol</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Category</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">State</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">1H (Trend)</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">30m (Setup)</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">15m (Confirm)</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">5m (Entry)</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Updated</th>
            </tr>
          </thead>
          <tbody id="signal-ladder-body">
            <tr><td colspan="8" style="text-align: center; color: var(--muted); padding:12px;">Loading active signals...</td></tr>
          </tbody>
        </table>
      </div>
    </div>"""

new_html = """    <div class="card" style="margin-bottom:28px; padding:16px;">
      <div style="margin-bottom: 12px; display: flex; gap: 16px; font-size: 12px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--border);">
        <label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-hourly" checked onchange="renderSignalLadder()"> Hourly Passed (1H)</label>
        <label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-setup" checked onchange="renderSignalLadder()"> Setup Armed (30m)</label>
        <label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-entry" checked onchange="renderSignalLadder()"> Entry Ready (15m)</label>
        <label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-triggered" checked onchange="renderSignalLadder()"> Triggered (5m)</label>
      </div>
      <div style="max-height: 250px; overflow-y: auto;">
        <table class="data-table" id="signal-ladder-table" style="width:100%; text-align:left; font-size:12px;">
          <thead>
            <tr>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Symbol</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">State</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Updated</th>
            </tr>
          </thead>
          <tbody id="signal-ladder-body">
            <tr><td colspan="3" style="text-align: center; color: var(--muted); padding:12px;">Loading active signals...</td></tr>
          </tbody>
        </table>
      </div>
    </div>"""

if old_html not in content:
    print("HTML block not found in admin_dashboard.html")
else:
    content = content.replace(old_html, new_html)
    with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/admin_dashboard.html', 'w') as f:
        f.write(content)
    print("HTML updated in admin_dashboard.html")
