import re

with open('app/admin_dashboard.html', 'r') as f:
    content = f.read()

funcs_to_async = ['clearLogs', 'clearNotifications', 'approveUser', 'rejectUser', 'submitDepositFunds']
for func in funcs_to_async:
    content = re.sub(rf'function {func}\(', f'async function {func}(', content)

def confirm_replacer(match):
    msg = match.group(1)
    suffix = match.group(2) 
    
    if 'return' in suffix:
        return f'const confirmed = await showCustomConfirm({msg});\n      if (!confirmed) return;'
    else:
        return f'const confirmed = await showCustomConfirm({msg});\n      if (!confirmed) {suffix}'

content = re.sub(r'if\s*\(\s*!\s*confirm\((.*?)\)\)\s*(return;|{)', confirm_replacer, content)

def alert_replacer(match):
    msg = match.group(1)
    lower_msg = msg.lower()
    if 'error' in lower_msg or 'failed' in lower_msg or 'denied' in lower_msg:
        toast_type = "'error'"
    elif 'success' in lower_msg or '✓' in lower_msg or '!' in lower_msg:
        toast_type = "'success'"
    else:
        toast_type = "'info'"
    return f'showCustomToast({msg}, {toast_type})'

# Avoid replacing our new showCustomToast if it gets matched? 
# Wait, I shouldn't replace alert in HTML attributes blindly!
# Wait, some alerts are like `onclick="alert('Error: ' + ...)"`
# Await won't work in onclick unless the onclick handler is marked async!
# Actually, for onclick, we can just let it call showCustomToast without await! showCustomToast isn't async anyway.
content = re.sub(r'alert\((.*?)\)', alert_replacer, content)

with open('app/admin_dashboard.html', 'w') as f:
    f.write(content)
