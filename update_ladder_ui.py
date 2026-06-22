import re

files = [
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/user_dashboard.html',
    '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/admin_dashboard.html'
]

for filepath in files:
    with open(filepath, 'r') as f:
        content = f.read()

    # Update Table Headers
    old_thead = """            <tr>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Symbol</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">State</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Updated</th>
            </tr>"""
    
    new_thead = """            <tr>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Symbol</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">State</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">CMP</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Updated</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border); text-align:right;">Chart</th>
            </tr>"""
    content = content.replace(old_thead, new_thead)

    # Update row.innerHTML in renderSignalLadder
    old_row = """    row.innerHTML = `
      <td style="font-weight:bold; padding:8px 0; border-bottom:1px solid var(--border);">${item.symbol}</td>
      <td style="font-weight:bold; color: ${stateColor}; padding:8px 0; border-bottom:1px solid var(--border);">${item.current_state}</td>
      <td style="font-family:var(--font-mono); font-size:10px; padding:8px 0; border-bottom:1px solid var(--border);">${timeStr}</td>
    `;"""

    new_row = """    const cmpStr = item.cmp ? '₹' + parseFloat(item.cmp).toFixed(2) : '—';
    row.innerHTML = `
      <td style="font-weight:bold; padding:8px 0; border-bottom:1px solid var(--border); cursor:pointer; text-decoration:underline;" onclick="openChart('${item.symbol}', ${item.cmp||0})">${item.symbol}</td>
      <td style="font-weight:bold; color: ${stateColor}; padding:8px 0; border-bottom:1px solid var(--border);">${item.current_state}</td>
      <td style="font-family:var(--font-mono); font-size:11px; font-weight:600; padding:8px 0; border-bottom:1px solid var(--border);">${cmpStr}</td>
      <td style="font-family:var(--font-mono); font-size:10px; padding:8px 0; border-bottom:1px solid var(--border);">${timeStr}</td>
      <td style="padding:8px 0; border-bottom:1px solid var(--border); text-align:right;">
        <button onclick="openChart('${item.symbol}', ${item.cmp||0})" style="background:var(--card2); border:1px solid var(--border); color:var(--text); padding:4px 8px; border-radius:4px; font-size:10px; cursor:pointer;">📈 Chart</button>
      </td>
    `;"""
    content = content.replace(old_row, new_row)

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Patched {filepath}")

