import re

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html', 'r') as f:
    content = f.read()

start_idx = content.find("window.signalLadderData = [];")
end_idx = content.find("async function fetchActivePositions()")

if start_idx != -1 and end_idx != -1:
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
  if(!tbody) return;
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
      <td style="font-family:var(--font-mono); font-size:10px; padding:8px 0; border-bottom:1px solid var(--border);">${item.last_updated ? new Date(item.last_updated).toLocaleTimeString('en-IN') : '-'}</td>
    `;
    tbody.appendChild(row);
  });
}

"""
    content = content[:start_idx] + new_js + content[end_idx:]
    with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html', 'w') as f:
        f.write(content)
    print("user_dashboard.html fixed!")
else:
    print("Could not find boundaries")
