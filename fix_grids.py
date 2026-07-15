import re

files = [
    'app/admin_dashboard.html',
    'app/user_dashboard.html',
    'app/wealth_dashboard.html'
]

# We need to replace:
# grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
# grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
# with flexbox
# For .scanner-grid and other grids.
# Wait, replacing CSS grid with flexbox requires changing display: grid to display: flex and adding flex-wrap: wrap.
# Then the children need flex: 1 1 280px.
# This is hard to do with regex without breaking things. It's safer to just change the grid-template-columns rule to use the modern grid auto-fill stretch hack, or stick to flexbox by injecting a CSS class.

def fix_css(content):
    # Instead of completely ripping out CSS grid and adding flex on children (which requires touching all child HTML),
    # the rule says: "ALWAYS use Flexbox (display: flex; flex-wrap: wrap;) with flex: 1 1 <basis> (or use nth-child grid hacks) so that the last row automatically stretches..."
    # The nth-child grid hack for filling the last row in CSS Grid:
    # Actually, replacing display: grid with display: flex; flex-wrap: wrap; on .scanner-grid, and adding a child selector .scanner-grid > * { flex: 1 1 280px; box-sizing: border-box; }
    
    # 1. Replace scanner-grid css
    content = re.sub(
        r'\.scanner-grid\s*\{.*?(display\s*:\s*grid|grid-template-columns.*?).*?\}',
        r'.scanner-grid { display: flex; flex-wrap: wrap; gap: 24px; }\n      .scanner-grid > * { flex: 1 1 280px; box-sizing: border-box; max-width: 100%; }',
        content, flags=re.DOTALL
    )
    
    # 2. Replace inline styles for dynamic grids
    content = re.sub(
        r'display:\s*grid;\s*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(\s*280px,\s*1fr\)\);',
        r'display: flex; flex-wrap: wrap;',
        content
    )
    # Also need to add flex: 1 1 280px to the children of that inline grid.
    # The inline grids usually render cards: html += `<div class="scanner-card">...</div>`
    # Let's just add flex: 1 1 280px to .scanner-card!
    if '.scanner-card {' in content and 'flex: 1 1 280px' not in content:
        content = content.replace('.scanner-card {', '.scanner-card { flex: 1 1 280px; box-sizing: border-box;')
        
    return content

for path in files:
    try:
        with open(path, 'r') as f:
            c = f.read()
        c = fix_css(c)
        with open(path, 'w') as f:
            f.write(c)
        print(f"Fixed grids in {path}")
    except Exception as e:
        print(e)
