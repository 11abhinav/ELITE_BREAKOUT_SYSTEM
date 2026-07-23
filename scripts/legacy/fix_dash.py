import re

paths = ['app/wealth_dashboard.html', 'app/admin_dashboard.html']
for path in paths:
    with open(path, 'r') as f:
        content = f.read()
    
    # Remove the hyphen from the ternary
    content = content.replace("${window.DATA_GENERATED_AT ? fmtDetailTime(window.DATA_GENERATED_AT) : '—'}", 
                              "${window.DATA_GENERATED_AT ? fmtDetailTime(window.DATA_GENERATED_AT) : ''}")
    
    with open(path, 'w') as f:
        f.write(content)
    print(f"Fixed {path}")
