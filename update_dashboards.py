import re

files = [
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html',
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/admin_dashboard.html'
]

for filepath in files:
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. Add span to checkboxes
    content = content.replace(
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-hourly" checked onchange="renderSignalLadder()"> Hourly Passed (1H)</label>',
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-hourly" checked onchange="renderSignalLadder()"> <span id="label-hourly">Hourly Passed (1H) (0)</span></label>'
    )
    content = content.replace(
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-setup" checked onchange="renderSignalLadder()"> Setup Armed (30m)</label>',
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-setup" checked onchange="renderSignalLadder()"> <span id="label-setup">Setup Armed (30m) (0)</span></label>'
    )
    content = content.replace(
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-entry" checked onchange="renderSignalLadder()"> Entry Ready (15m)</label>',
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-entry" checked onchange="renderSignalLadder()"> <span id="label-entry">Entry Ready (15m) (0)</span></label>'
    )
    content = content.replace(
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-triggered" checked onchange="renderSignalLadder()"> Triggered (5m)</label>',
        '<label style="cursor:pointer; display:flex; align-items:center; gap:4px;"><input type="checkbox" id="filter-triggered" checked onchange="renderSignalLadder()"> <span id="label-triggered">Triggered (5m) (0)</span></label>'
    )

    # 2. Add id="tvModalTitle"
    content = content.replace(
        '<div style="color:var(--text); font-family:var(--font-mono); font-weight:700; font-size:18px; letter-spacing:1px;">LIVE INTERACTIVE CHART</div>',
        '<div id="tvModalTitle" style="color:var(--text); font-family:var(--font-mono); font-weight:700; font-size:18px; letter-spacing:1px;">LIVE INTERACTIVE CHART</div>'
    )

    # 3. Update openChart function
    old_open_chart = """function openChart(symbol) {
    document.getElementById('tvModal').style.display = 'flex';
    document.getElementById('tv-container').innerHTML = '';
    const newTabLink = document.getElementById('tvModalNewTab');
    if (newTabLink) newTabLink.href = 'https://in.tradingview.com/chart/?symbol=NSE:' + symbol;"""

    new_open_chart = """function openChart(symbol, cmp) {
    document.getElementById('tvModal').style.display = 'flex';
    document.getElementById('tv-container').innerHTML = '';
    const titleEl = document.getElementById('tvModalTitle');
    if (titleEl) {
        if (cmp != null && cmp !== 'undefined') {
            titleEl.innerHTML = `LIVE INTERACTIVE CHART - <span style="color:var(--accent)">${symbol}</span> <span style="font-size:12px;color:var(--muted);margin-left:12px;">CMP: ₹${cmp}</span>`;
        } else {
            titleEl.innerHTML = `LIVE INTERACTIVE CHART - <span style="color:var(--accent)">${symbol}</span>`;
        }
    }
    const newTabLink = document.getElementById('tvModalNewTab');
    if (newTabLink) newTabLink.href = 'https://in.tradingview.com/chart/?symbol=NSE:' + symbol;"""

    content = content.replace(old_open_chart, new_open_chart)

    # 4. Update the openChart call in row template
    content = content.replace(
        '<button onclick="openChart(\'${t.symbol}\')" style="background:var(--accent); color:#000; border:none; padding:4px 10px; border-radius:4px; font-size:10px; font-weight:700; cursor:pointer; max-width:max-content;">📊 In-App Chart</button>',
        '<button onclick="openChart(\'${t.symbol}\', ${t.current_price})" style="background:var(--accent); color:#000; border:none; padding:4px 10px; border-radius:4px; font-size:10px; font-weight:700; cursor:pointer; max-width:max-content;">📊 In-App Chart</button>'
    )
    
    # 5. Update renderSignalLadder to show counts
    old_render = """function renderSignalLadder() {
  const tbody = document.getElementById('signal-ladder-body');
  if(!tbody) return;
  tbody.innerHTML = '';
  
  if(!window.signalLadderData || window.signalLadderData.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--muted); padding:12px;">No active setups tracking right now.</td></tr>';
    return;
  }
  
  const showHourly = document.getElementById('filter-hourly')?.checked;"""
    
    new_render = """function renderSignalLadder() {
  const tbody = document.getElementById('signal-ladder-body');
  if(!tbody) return;
  tbody.innerHTML = '';
  
  let countHourly = 0;
  let countSetup = 0;
  let countEntry = 0;
  let countTriggered = 0;
  
  if(window.signalLadderData) {
      window.signalLadderData.forEach(item => {
        if (item.current_state === 'HOURLY_APPROVED') countHourly++;
        else if (item.current_state === 'SETUP_ARMED') countSetup++;
        else if (item.current_state === 'ENTRY_READY') countEntry++;
        else if (item.current_state === 'BREAKOUT_CONFIRMED' || item.current_state === 'TRIGGERED') countTriggered++;
      });
  }

  const labelHourly = document.getElementById('label-hourly');
  if (labelHourly) labelHourly.textContent = `Hourly Passed (1H) (${countHourly})`;
  const labelSetup = document.getElementById('label-setup');
  if (labelSetup) labelSetup.textContent = `Setup Armed (30m) (${countSetup})`;
  const labelEntry = document.getElementById('label-entry');
  if (labelEntry) labelEntry.textContent = `Entry Ready (15m) (${countEntry})`;
  const labelTriggered = document.getElementById('label-triggered');
  if (labelTriggered) labelTriggered.textContent = `Triggered (5m) (${countTriggered})`;

  if(!window.signalLadderData || window.signalLadderData.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--muted); padding:12px;">No active setups tracking right now.</td></tr>';
    return;
  }
  
  const showHourly = document.getElementById('filter-hourly')?.checked;"""

    content = content.replace(old_render, new_render)

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Updated {filepath}")

