import re

CSS = """<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');
  
  :root {
    --bg:         #f6f8fb;
    --surface:    #ffffff;
    --card:       #ffffff;
    --card2:      #f8fafc;
    --card3:      #f1f5f9;
    --border:     #e2e8f0;
    --border2:    #cbd5e1;
    --accent:       #7c3aed;
    --accent-dim:   rgba(124,58,237,0.08);
    --accent-hover: #6d28d9;
    --win:      #059669;
    --loss:     #dc2626;
    --open:     #2563eb;
    --neutral:  #64748b;
    --down:     #dc2626;
    --warn:     #dc2626;
    --warn-dim: rgba(220,38,38,0.08);
    --amber:    #d97706;
    --amber-dim:rgba(217,119,6,0.08);
    --blue:     #3b82f6;
    --green:    #10b981;
    --red:      #ef4444;
    --text:   #0f172a;
    --muted:  #64748b;
    --muted2: #94a3b8;
    --font-body: 'Inter', system-ui, -apple-system, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
    --radius:    8px;
    --radius-sm: 5px;
    --radius-lg: 12px;
    --shadow:    0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md: 0 4px 12px rgba(0,0,0,.06), 0 2px 4px rgba(0,0,0,.04);
    --shadow-lg: 0 8px 24px rgba(0,0,0,.08), 0 4px 8px rgba(0,0,0,.04);
    --glow:      0 0 0 3px rgba(124,58,237,0.15);
  }

  [data-theme="dark"] {
    --bg:      #0d1117;
    --surface: #161b22;
    --card:    #1c2128;
    --card2:   #21262d;
    --card3:   #2d333b;
    --border:  rgba(255,255,255,.08);
    --border2: rgba(255,255,255,.14);
    --text:    #e6edf3;
    --muted:   #8b949e;
    --muted2:  #6e7681;
    --shadow:  0 1px 3px rgba(0,0,0,.3);
    --shadow-md: 0 4px 12px rgba(0,0,0,.3);
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-body); font-size: 14px; min-height: 100vh; -webkit-font-smoothing: antialiased; }
  html, body { max-width: 100vw !important; width: 100vw !important; overflow-x: hidden !important; position: relative; }
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted2); }
  ::selection { background: var(--accent); color: #fff; }

  /* HEADER */
  .header {
    background: var(--card); border-bottom: 1px solid var(--border);
    padding: 0 28px; height: 58px; display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 50; backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  }
  .header-left { display: flex; align-items: center; gap: 24px; }
  .title { font-family: var(--font-body); font-size: 15px; font-weight: 700; letter-spacing: -0.3px; color: var(--text); }
  .title span { color: var(--accent); }
  .subtitle { font-size: 11px; color: var(--muted); font-family: var(--font-mono); margin-top: 1px; letter-spacing: 0; }
  .header-nav { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .nav-btn, .guide-btn {
    background: var(--card2); border: 1px solid var(--border); color: var(--text);
    padding: 7px 13px; border-radius: var(--radius); cursor: pointer; font-size: 12px;
    font-family: var(--font-body); font-weight: 500; transition: all .18s; display: inline-flex; align-items: center; gap: 6px;
  }
  .nav-btn:hover, .guide-btn:hover { border-color: var(--border2); background: var(--card3); box-shadow: var(--shadow); }

  /* MACRO BADGE */
  #macro-state-badge {
    font-size: 11px; padding: 6px 12px; border-radius: 20px; font-weight: 600;
    font-family: var(--font-mono); letter-spacing: 0.5px; border: 1px solid rgba(16,185,129,0.3);
    background: rgba(16,185,129,0.1); color: var(--green);
  }

  /* SCORE BAR */
  .weight-bar { display: flex; gap: 0; border-radius: var(--radius-lg); overflow: hidden; height: 28px; margin: 24px 28px; font-size: 10px; font-weight: 700; font-family: var(--font-mono); box-shadow: var(--shadow); }
  .weight-bar div { display: flex; align-items: center; justify-content: center; color: #fff; transition: flex 0.3s ease, filter 0.3s ease; cursor: pointer; }
  .weight-bar div:hover { filter: brightness(1.1); flex: 1.1 !important; }

  /* KPI GRID */
  .kpi-grid { display: flex; flex-wrap: wrap; gap: 16px; margin: 0 28px 32px; padding: 0; }
  .kpi-card {
    flex: 1 1 160px; background: var(--card); border: 1px solid var(--border); border-left: 3px solid var(--border2);
    border-radius: var(--radius); padding: 16px 18px; text-align: left;
    transition: all .18s; cursor: pointer; position: relative; overflow: hidden;
  }
  .kpi-card:hover { border-color: var(--border2); border-left-color: var(--accent); box-shadow: var(--shadow-md); transform: translateY(-1px); }
  .kpi-card.alert-blink { animation: kpiPulse 2s infinite; border-left-color: var(--warn); }
  @keyframes kpiPulse { 0%{box-shadow: 0 0 0 rgba(220,38,38,0.2)} 50%{box-shadow: 0 0 16px rgba(220,38,38,0.4)} 100%{box-shadow: 0 0 0 rgba(220,38,38,0.2)} }
  .kpi-value { font-family: var(--font-mono); font-size: 24px; font-weight: 700; color: var(--text); margin-bottom: 8px; line-height: 1; letter-spacing: -0.5px; }
  .kpi-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; font-weight: 600; }

  /* TABS */
  .tab-bar { display: flex; gap: 8px; margin: 0 28px 24px; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
  .tab-btn {
    background: transparent; border: none; color: var(--muted); padding: 12px 18px;
    font-family: var(--font-body); font-size: 13px; font-weight: 600; cursor: pointer;
    margin-bottom: -1px; transition: all .2s; border-bottom: 2px solid transparent;
  }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-panel { display: none; padding: 0 28px 28px 28px; }
  .tab-panel.active { display: block; animation: fadeIn 0.2s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

  /* SECTION TITLES */
  .section-title {
    font-family: var(--font-body); font-size: 15px; font-weight: 700; color: var(--text);
    margin-bottom: 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  .section-title span {
    font-size: 10px; padding: 4px 10px; background: var(--card2); border: 1px solid var(--border);
    border-radius: 20px; color: var(--muted); font-family: var(--font-mono); font-weight: 600; letter-spacing: 0.5px;
  }
  .section-title .tag-red { color: var(--warn); border-color: rgba(220,38,38,0.2); background: var(--warn-dim); }
  .section-title .tag-green { color: var(--win); border-color: rgba(5,150,105,0.2); background: rgba(5,150,105,0.1); }

  /* TABLES */
  .table-container {
    background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg);
    overflow-x: auto; margin-bottom: 32px; box-shadow: var(--shadow);
  }
  table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
  th, td { padding: 11px 12px; white-space: nowrap; }
  th { background: var(--card2); color: var(--muted); font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; border-bottom: 1px solid var(--border); cursor: pointer; transition: color .15s; }
  th:hover { color: var(--text); }
  tr:last-child td { border-bottom: none; }
  tbody tr { transition: background .08s; border-bottom: 1px solid var(--border); }
  tbody tr:hover { background: var(--card2); }
  tbody tr.buy-signal-blink { animation: buySignalBlink 2s infinite; border-left: 3px solid var(--win); background: rgba(5,150,105,0.05); }
  @keyframes buySignalBlink { 0%{box-shadow: inset 2px 0 0 var(--win)} 50%{box-shadow: inset 2px 0 0 transparent} 100%{box-shadow: inset 2px 0 0 var(--win)} }

  .sym { font-family: var(--font-mono); font-weight: 700; color: var(--text); font-size: 13px; }
  .mono { font-family: var(--font-mono); font-size: 12px; }
  .score-badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-family: var(--font-mono); font-weight: 700; font-size: 10px; letter-spacing: .3px; }
  .score-high { background: rgba(5,150,105,.1); color: var(--win); border: 1px solid rgba(5,150,105,.2); }
  .score-med { background: rgba(217,119,6,.1); color: var(--amber); border: 1px solid rgba(217,119,6,.2); }
  .score-low { background: rgba(220,38,38,.1); color: var(--warn); border: 1px solid rgba(220,38,38,.2); }
  
  .signal { display: inline-block; font-size: 10px; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-family: var(--font-mono); letter-spacing: .3px; cursor: pointer; }
  .sig-buy { background: rgba(5,150,105,.1); color: var(--win); border: 1px solid rgba(5,150,105,.2); }
  .sig-sell { background: rgba(220,38,38,.1); color: var(--warn); border: 1px solid rgba(220,38,38,.2); }
  
  .rs-pos { color: var(--win); font-family: var(--font-mono); font-weight: 600; }
  .rs-neg { color: var(--loss); font-family: var(--font-mono); font-weight: 600; }
  .empty { padding: 32px; text-align: center; color: var(--muted); font-size: 13px; }

  .sector-tag {
    display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 10px;
    background: rgba(37,99,235,.1); color: var(--open); border: 1px solid rgba(37,99,235,.2);
    font-family: var(--font-mono); font-weight: 700; letter-spacing: .3px;
  }

  /* ALERTS & NOTIFS */
  .alert-toast { position: fixed; top: -100px; left: 50%; transform: translateX(-50%); background: var(--warn); color: #fff; padding: 14px 22px; border-radius: var(--radius); font-weight: 700; z-index: 10000; display: flex; align-items: center; gap: 12px; box-shadow: 0 10px 28px rgba(220,38,38,0.45); transition: top 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275); cursor: pointer; }
  .alert-toast.show { top: 16px; animation: pulseToast 2s infinite; }
  .alert-toast-icon { font-size: 22px; animation: spin 2s linear infinite; }
  
  .exit-banner { background: var(--warn-dim); border: 1px solid rgba(220,38,38,0.2); border-radius: var(--radius); padding: 14px 18px; margin-bottom: 20px; font-size: 13px; color: var(--muted); line-height: 1.5; }
  .exit-banner strong { color: var(--warn); }
  .alert-blink { animation: kpiPulse 1s infinite; background: rgba(255,235,59,0.08); }

  /* NOTIFICATION BELL */
  .notification-bell-container { position: relative; display: inline-block; margin-right: 6px; cursor: pointer; }
  .notification-bell-icon { width: 34px; height: 34px; border-radius: var(--radius); background: var(--card2); border: 1px solid var(--border); display: flex; align-items: center; justify-content: center; color: var(--muted); transition: all 0.18s; }
  .notification-bell-icon:hover { background: var(--accent-dim); color: var(--accent); border-color: rgba(124,58,237,0.3); }
  .notification-badge { position: absolute; top: -4px; right: -4px; background: var(--warn); color: white; font-size: 10px; font-weight: 700; border-radius: 50%; width: 17px; height: 17px; display: none; align-items: center; justify-content: center; box-shadow: 0 0 0 2px var(--bg); }
  .notification-badge.active { display: flex; animation: pulseBadge 2s infinite; }
  @keyframes pulseBadge { 0%{box-shadow:0 0 0 0 rgba(220,38,38,0.6),0 0 0 2px var(--bg);} 70%{box-shadow:0 0 0 5px rgba(220,38,38,0),0 0 0 2px var(--bg);} 100%{box-shadow:0 0 0 0 rgba(220,38,38,0),0 0 0 2px var(--bg);} }
  .notification-dropdown { position: absolute; top: 42px; right: 0; width: 330px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-lg); z-index: 1000; display: none; overflow: hidden; }
  .notification-dropdown.show { display: block; animation: dropIn 0.18s ease-out; }
  @keyframes dropIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
  .notification-header { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: var(--card2); }
  .notification-header h3 { margin: 0; font-size: 13px; font-weight: 700; }
  .mark-all-seen { font-size: 11px; color: var(--accent); cursor: pointer; font-weight: 600; }
  .notification-list { max-height: 400px; overflow-y: auto; }
  .notification-item { padding: 12px 16px; border-bottom: 1px solid var(--border); transition: background 0.15s; }
  .notification-item:last-child { border-bottom: none; }
  .notification-item:hover { background: var(--card2); }
  .notification-item.unseen { background: var(--accent-dim); border-left: 3px solid var(--accent); }
  .notification-item-title { font-weight: 600; font-size: 13px; margin-bottom: 3px; color: var(--text); }
  .notification-item-msg { font-size: 12px; color: var(--muted); margin-bottom: 5px; line-height: 1.4; }

  @media(max-width: 768px) {
    .header { height: auto; padding: 12px 16px; flex-direction: column; align-items: flex-start; gap: 12px; }
    .header-nav { width: 100%; justify-content: space-between; }
    .kpi-grid { margin: 0 16px 24px; }
    .tab-bar { margin: 0 16px 16px; }
    .tab-panel { padding: 0 16px 16px; }
    .table-container { overflow-x: auto; max-width: calc(100vw - 32px); }
    .notification-dropdown { position: fixed; top: 60px; left: 50%; transform: translateX(-50%); width: 95vw; right: auto; }
  }
</style>"""

with open('app/wealth_dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the first style block in the document and replace it
start_idx = content.find('<style>')
end_idx = content.find('</style>') + len('</style>')

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + CSS + content[end_idx:]
    with open('app/wealth_dashboard.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("CSS replaced successfully!")
else:
    print("Could not find style block")
