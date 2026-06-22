import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html', 'r') as f:
    content = f.read()

# Replace HTML
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

content = content.replace(old_html, new_html)

# Replace JS
old_js = """async function fetchSignalLadder() {
  try {
    const res = await fetch('/api/breakout_watchlist');
    if(!res.ok) return;
    const json = await res.json();
    if(json.status !== "success") return;
    
    const tbody = document.getElementById('signal-ladder-body');
    tbody.innerHTML = '';
    
    if(!json.data || json.data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--muted); padding:12px;">No active setups tracking right now.</td></tr>';
      return;
    }
    
    json.data.forEach(item => {
      const row = document.createElement('tr');
      
      let stateColor = 'var(--text)';
      if(item.current_state === 'HOURLY_APPROVED') stateColor = 'var(--blue)';
      else if(item.current_state === 'SETUP_ARMED') stateColor = 'var(--amber)';
      else if(item.current_state === 'BREAKOUT_CONFIRMED') stateColor = 'var(--green)';
      else if(item.current_state === 'ENTRY_READY') stateColor = 'var(--accent)';
      else if(item.current_state === 'FAILED') stateColor = 'var(--red)';
      
      row.innerHTML = `
        <td style="font-weight:bold; padding:8px 0; border-bottom:1px solid var(--border);">${item.symbol}</td>
        <td style="font-size:11px; padding:8px 0; border-bottom:1px solid var(--border);">${item.category || '-'}</td>
        <td style="font-weight:bold; color: ${stateColor}; padding:8px 0; border-bottom:1px solid var(--border);">${item.current_state}</td>
        <td style="font-size:11px; padding:8px 0; border-bottom:1px solid var(--border);">${item.h1_status}</td>
        <td style="font-size:11px; padding:8px 0; border-bottom:1px solid var(--border);">${item.m30_status}</td>
        <td style="font-size:11px; padding:8px 0; border-bottom:1px solid var(--border);">${item.m15_status}</td>
        <td style="font-size:11px; padding:8px 0; border-bottom:1px solid var(--border);">${item.m5_status}</td>
        <td style="font-family:var(--font-mono); font-size:10px; padding:8px 0; border-bottom:1px solid var(--border);">${item.last_updated ? item.last_updated.substring(11, 19) : '-'}</td>
      `;
      tbody.appendChild(row);
    });
  } catch(e) {
    console.error("Error fetching signal ladder:", e);
  }
}"""

new_js = """window.signalLadderData = [];

async function fetchSignalLadder() {
  try {
    const res = await fetch('/api/breakout_watchlist');
    if(!res.ok) return;
    const json = await res.json();
    if(json.status !== "success") return;
    
    window.signalLadderData = json.data || [];
    renderSignalLadder();
  } catch(e) {
    console.error("Error fetching signal ladder:", e);
  }
}

function renderSignalLadder() {
  const tbody = document.getElementById('signal-ladder-body');
  tbody.innerHTML = '';
  
  if(!window.signalLadderData || window.signalLadderData.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--muted); padding:12px;">No active setups tracking right now.</td></tr>';
    return;
  }
  
  const showHourly = document.getElementById('filter-hourly')?.checked;
  const showSetup = document.getElementById('filter-setup')?.checked;
  const showEntry = document.getElementById('filter-entry')?.checked;
  const showTriggered = document.getElementById('filter-triggered')?.checked;

  let filtered = window.signalLadderData.filter(item => {
    if (item.current_state === 'HOURLY_APPROVED') return showHourly;
    if (item.current_state === 'SETUP_ARMED') return showSetup;
    if (item.current_state === 'ENTRY_READY') return showEntry;
    if (item.current_state === 'BREAKOUT_CONFIRMED' || item.current_state === 'TRIGGERED') return showTriggered;
    return true; // FAILED or others
  });

  if(filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--muted); padding:12px;">No setups match selected filters.</td></tr>';
    return;
  }
  
  filtered.forEach(item => {
    const row = document.createElement('tr');
    
    let stateColor = 'var(--text)';
    if(item.current_state === 'HOURLY_APPROVED') stateColor = 'var(--blue)';
    else if(item.current_state === 'SETUP_ARMED') stateColor = 'var(--amber)';
    else if(item.current_state === 'BREAKOUT_CONFIRMED' || item.current_state === 'TRIGGERED') stateColor = 'var(--green)';
    else if(item.current_state === 'ENTRY_READY') stateColor = 'var(--accent)';
    else if(item.current_state === 'FAILED') stateColor = 'var(--red)';
    
    row.innerHTML = `
      <td style="font-weight:bold; padding:8px 0; border-bottom:1px solid var(--border);">${item.symbol}</td>
      <td style="font-weight:bold; color: ${stateColor}; padding:8px 0; border-bottom:1px solid var(--border);">${item.current_state}</td>
      <td style="font-family:var(--font-mono); font-size:10px; padding:8px 0; border-bottom:1px solid var(--border);">${item.last_updated ? item.last_updated.substring(11, 19) : '-'}</td>
    `;
    tbody.appendChild(row);
  });
}"""

if old_js not in content:
    print("JS not found in user_dashboard.html")
else:
    content = content.replace(old_js, new_js)
    with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html', 'w') as f:
        f.write(content)
    print("user_dashboard.html updated")

