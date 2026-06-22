import re
import os

DASHBOARDS = [
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/admin_dashboard.html',
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html',
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/wealth_dashboard.html'
]

CSS = """
  /* Notification Center Styles */
  .notification-bell-container {
    position: relative;
    display: inline-block;
    margin-right: 15px;
    cursor: pointer;
  }
  .notification-bell-icon {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    background: var(--card2);
    border: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text);
    transition: all 0.2s;
  }
  .notification-bell-icon:hover {
    background: var(--accent-dim);
    color: var(--accent);
    border-color: var(--accent);
  }
  .notification-badge {
    position: absolute;
    top: -5px;
    right: -5px;
    background: var(--warn);
    color: white;
    font-size: 10px;
    font-weight: bold;
    border-radius: 50%;
    width: 18px;
    height: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 0 0 2px var(--bg);
    display: none;
  }
  .notification-badge.active {
    display: flex;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.7); }
    70% { box-shadow: 0 0 0 6px rgba(225, 29, 72, 0); }
    100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); }
  }
  .notification-dropdown {
    position: absolute;
    top: 45px;
    right: 0;
    width: 320px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: var(--shadow);
    z-index: 1000;
    display: none;
    overflow: hidden;
  }
  .notification-dropdown.show {
    display: block;
    animation: slideDown 0.2s ease-out;
  }
  @keyframes slideDown {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .notification-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--card2);
  }
  .notification-header h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
  }
  .mark-all-seen {
    font-size: 11px;
    color: var(--accent);
    cursor: pointer;
    font-weight: 600;
  }
  .notification-list {
    max-height: 400px;
    overflow-y: auto;
  }
  .notification-item {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    transition: background 0.2s;
  }
  .notification-item:last-child {
    border-bottom: none;
  }
  .notification-item:hover {
    background: var(--card2);
  }
  .notification-item.unseen {
    background: var(--accent-dim);
    border-left: 3px solid var(--accent);
  }
  .notification-item-title {
    font-weight: 600;
    font-size: 13px;
    margin-bottom: 4px;
    color: var(--text);
  }
  .notification-item-msg {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
    line-height: 1.4;
  }
  .notification-item-meta {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: var(--muted2);
  }
  .notification-type-buy { color: var(--accent); }
  .notification-type-sell { color: var(--warn); }
"""

HTML = """
      <!-- NOTIFICATION BELL -->
      <div class="notification-bell-container" id="notif-container">
        <div class="notification-bell-icon" onclick="toggleNotifications(event)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>
        </div>
        <div class="notification-badge" id="notif-badge">0</div>
        
        <div class="notification-dropdown" id="notif-dropdown">
          <div class="notification-header">
            <h3>Alerts & Notifications</h3>
            <span class="mark-all-seen" onclick="markAllNotificationsSeen(event)">Mark all seen</span>
          </div>
          <div class="notification-list" id="notif-list">
            <!-- Items injected by JS -->
            <div style="padding:20px; text-align:center; color:var(--muted); font-size:12px;">No notifications yet.</div>
          </div>
        </div>
      </div>
      <!-- END NOTIFICATION BELL -->
"""

JS = """
// ============================================================================
// NOTIFICATION SYSTEM
// ============================================================================
let lastNotifIds = new Set();
let isFirstNotifLoad = true;
let isAudioEnabled = false;

// We use an external loud alarm sound
const alertAudio = new Audio('https://actions.google.com/sounds/v1/alarms/digital_watch_alarm_long.ogg');
alertAudio.volume = 1.0;

// Enable audio on first user interaction to bypass browser autoplay blocks
document.addEventListener('click', () => {
    if(!isAudioEnabled) {
        alertAudio.load();
        isAudioEnabled = true;
    }
}, {once:true});

function toggleNotifications(e) {
    if(e) e.stopPropagation();
    const dropdown = document.getElementById('notif-dropdown');
    dropdown.classList.toggle('show');
}

document.addEventListener('click', (e) => {
    const container = document.getElementById('notif-container');
    const dropdown = document.getElementById('notif-dropdown');
    if (container && !container.contains(e.target)) {
        dropdown.classList.remove('show');
    }
});

async function fetchNotifications() {
    try {
        const res = await fetch('/api/notifications');
        if (!res.ok) return;
        const data = await res.json();
        
        const list = document.getElementById('notif-list');
        const badge = document.getElementById('notif-badge');
        if(!list || !badge) return;
        
        let unseenCount = 0;
        let newNotifs = false;
        
        if (data.length === 0) {
            list.innerHTML = '<div style="padding:20px; text-align:center; color:var(--muted); font-size:12px;">No notifications yet.</div>';
            return;
        }
        
        let html = '';
        const currentIds = new Set();
        
        data.forEach(n => {
            currentIds.add(n.id);
            if (!n.is_seen) unseenCount++;
            
            // Check for completely new notifications (not just seen status changes)
            if (!isFirstNotifLoad && !lastNotifIds.has(n.id)) {
                newNotifs = true;
            }
            
            const typeClass = n.type === 'buy' ? 'notification-type-buy' : 'notification-type-sell';
            const unseenClass = !n.is_seen ? 'unseen' : '';
            
            html += `
                <div class="notification-item ${unseenClass}" onclick="markNotificationSeen(${n.id}, '${n.symbol}')">
                    <div class="notification-item-title ${typeClass}">${n.title}</div>
                    <div class="notification-item-msg">${n.message}</div>
                    <div class="notification-item-meta">
                        <span>${n.symbol || ''}</span>
                        <span>${n.created_at || ''}</span>
                    </div>
                </div>
            `;
        });
        
        list.innerHTML = html;
        
        if (unseenCount > 0) {
            badge.textContent = unseenCount > 99 ? '99+' : unseenCount;
            badge.classList.add('active');
        } else {
            badge.classList.remove('active');
        }
        
        // Play sound if there are NEW notifications
        if (newNotifs && isAudioEnabled) {
            alertAudio.play().catch(e => console.log('Audio blocked:', e));
        }
        
        lastNotifIds = currentIds;
        isFirstNotifLoad = false;
        
    } catch (err) {
        console.error('Notification poll error:', err);
    }
}

async function markNotificationSeen(id, symbol) {
    try {
        await fetch(`/api/notifications/mark_seen/${id}`, {method: 'POST'});
        fetchNotifications(); // refresh
        // Also pre-fill the search/filter if the user clicked it
        if(symbol && symbol !== 'null') {
            const searchInput = document.getElementById('search');
            if(searchInput) {
                searchInput.value = symbol;
                searchInput.dispatchEvent(new Event('input'));
            }
        }
        document.getElementById('notif-dropdown').classList.remove('show');
    } catch (e) {
        console.error(e);
    }
}

async function markAllNotificationsSeen(e) {
    if(e) e.stopPropagation();
    try {
        await fetch('/api/notifications/mark_all_seen', {method: 'POST'});
        fetchNotifications();
    } catch (e) {
        console.error(e);
    }
}

// Poll every 5 seconds
setInterval(fetchNotifications, 5000);
fetchNotifications();
// ============================================================================
"""

for path in DASHBOARDS:
    if not os.path.exists(path):
        print(f"Skipping {path}, does not exist.")
        continue
        
    with open(path, 'r') as f:
        code = f.read()
    
    modified = False
    
    # 1. Inject CSS
    if "/* Notification Center Styles */" not in code:
        code = code.replace("</style>", CSS + "\n</style>")
        modified = True
        
    # 2. Inject HTML (find header-right)
    if "<!-- NOTIFICATION BELL -->" not in code:
        if '<div class="header-right">' in code:
            code = code.replace('<div class="header-right">', '<div class="header-right">\n' + HTML)
            modified = True
        elif 'class="header-right"' in code:
            # Maybe slightly different syntax
            code = re.sub(r'(<div[^>]*class="[^"]*header-right[^"]*"[^>]*>)', r'\1\n' + HTML, code)
            modified = True

    # 3. Inject JS
    if "// NOTIFICATION SYSTEM" not in code:
        # Find closing script tag or just append before </body>
        if "</body>" in code:
            code = code.replace("</body>", f"<script>\n{JS}\n</script>\n</body>")
            modified = True
            
    if modified:
        with open(path, 'w') as f:
            f.write(code)
        print(f"Patched {path}")
    else:
        print(f"No changes needed for {path}")

