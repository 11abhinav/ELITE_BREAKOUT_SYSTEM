with open('app/admin_dashboard.html', 'r') as f:
    text = f.read()

replacements = {
    # 1. Access Denied alerts
    'alert("Access Denied: " + data.error)': 'showCustomToast("Access Denied: " + data.error, "error")',
    'alert("Access Denied.")': 'showCustomToast("Access Denied.", "error")',

    # 2. Pledge Worker Mode
    'if(!confirm(`Are you sure you want to change Pledge Worker mode to: ${mode}?`)) return;': 
        'const confirmed = await showCustomConfirm(`Are you sure you want to change Pledge Worker mode to: ${mode}?`);\n      if (!confirmed) return;',
    "alert('Failed to set mode: ' + (err.message || 'Unknown error'));": 
        "showCustomToast('Failed to set mode: ' + (err.message || 'Unknown error'), 'error');",

    # 3. HTML onclicks
    "onclick=\"alert('Error: ' + '${(data.error || '').replace(/'/g, \"\\\\'\")}')\"": 
        "onclick=\"showCustomToast('Error: ' + '${(data.error || '').replace(/'/g, \\\"\\\\\\\"')}', 'error')\"",
    
    "onclick=\"alert('Error Details:\\\\n\\\\n' + '${(data.error || '').replace(/'/g, \"\\\\'\").replace(/\\n/g, '\\\\n')}')\"": 
        "onclick=\"showCustomToast('Error Details: ' + '${(data.error || '').replace(/'/g, \\\"\\\\\\\"')}', 'error')\"",

    # 4. Reject Alert
    "if (!confirm('Are you sure you want to REJECT this trade? Capital will be refunded and it will not count towards KPIs.')) return;":
        "const confirmed = await showCustomConfirm('Are you sure you want to REJECT this trade? Capital will be refunded and it will not count towards KPIs.');\n      if (!confirmed) return;",
    "alert('Failed to reject trade.');": "showCustomToast('Failed to reject trade.', 'error');",
    "alert('Error rejecting trade.');": "showCustomToast('Error rejecting trade.', 'error');",

    # 5. Allocate Selected Trades
    "if (!confirm(`Are you sure you want to ALLOCATE capital for ${checkboxes.length} selected trades? This will read your current Cash In Hand and distribute it evenly.`)) return;":
        "const confirmed = await showCustomConfirm(`Are you sure you want to ALLOCATE capital for ${checkboxes.length} selected trades? This will read your current Cash In Hand and distribute it evenly.`);\n      if (!confirmed) return;",
    "alert('Failed to allocate trades.');": "showCustomToast('Failed to allocate trades.', 'error');",
    "alert('Error allocating trades.');": "showCustomToast('Error allocating trades.', 'error');",

    # 6. Reject Selected Trades
    "if (!confirm(`Are you sure you want to REJECT ${checkboxes.length} selected trades? Capital will be refunded and they will not count towards KPIs.`)) return;":
        "const confirmed = await showCustomConfirm(`Are you sure you want to REJECT ${checkboxes.length} selected trades? Capital will be refunded and they will not count towards KPIs.`);\n      if (!confirmed) return;",
    "alert('Failed to reject trades.');": "showCustomToast('Failed to reject trades.', 'error');",
    "alert('Error rejecting trades.');": "showCustomToast('Error rejecting trades.', 'error');",

    # 7. Accept Trade
    "if (!confirm('Are you sure you want to RE-ACCEPT this trade? Capital will be deducted.')) return;":
        "const confirmed = await showCustomConfirm('Are you sure you want to RE-ACCEPT this trade? Capital will be deducted.');\n      if (!confirmed) return;",
    "alert('Failed to accept trade.');": "showCustomToast('Failed to accept trade.', 'error');",
    "alert('Error accepting trade.');": "showCustomToast('Error accepting trade.', 'error');",

    # 8. Recalc Alloc
    "if (!confirm('Are you sure you want to recalculate and allocate capital for this trade? It will read your current Cash In Hand.')) return;":
        "const confirmed = await showCustomConfirm('Are you sure you want to recalculate and allocate capital for this trade? It will read your current Cash In Hand.');\n      if (!confirmed) return;",
    "alert('Failed to allocate capital.');": "showCustomToast('Failed to allocate capital.', 'error');",
    "alert('Error allocating capital.');": "showCustomToast('Error allocating capital.', 'error');",

    # 9. Clear Logs
    "function clearLogs() {": "async function clearLogs() {",
    'if(!confirm("Are you sure you want to clear all system logs?")) return;':
        'const confirmed = await showCustomConfirm("Are you sure you want to clear all system logs?");\n    if (!confirmed) return;',

    # 10. Acknowledge Error
    "if (!confirm('Mark this error as ignored? Count will reset to 0 and it will reappear if the error occurs again.')) {":
        "const confirmed = await showCustomConfirm('Mark this error as ignored? Count will reset to 0 and it will reappear if the error occurs again.');\n  if (!confirmed) {",
    "alert('Failed to acknowledge error');": "showCustomToast('Failed to acknowledge error', 'error');",
    "alert('Error acknowledging the error');": "showCustomToast('Error acknowledging the error', 'error');",

    # 11. Clear All Errors
    "if (!confirm('Clear ALL non-critical errors at once? This will acknowledge all errors and reset their counts.')) {":
        "const confirmed = await showCustomConfirm('Clear ALL non-critical errors at once? This will acknowledge all errors and reset their counts.');\n  if (!confirmed) {",
    "alert('Failed to clear all errors');": "showCustomToast('Failed to clear all errors', 'error');",
    "alert('Error clearing all errors');": "showCustomToast('Error clearing all errors', 'error');",

    # 12. Deposit Funds
    "alert('Please enter a valid amount');": "showCustomToast('Please enter a valid amount', 'info');",
    "alert(`✓ Deposited ₹${ amount.toLocaleString('en-IN') }. Total Capital: ₹${ Math.round(result.total_capital).toLocaleString('en-IN') } `);":
        "showCustomToast(`✓ Deposited ₹${ amount.toLocaleString('en-IN') }. Total Capital: ₹${ Math.round(result.total_capital).toLocaleString('en-IN') } `, 'success');",
    "alert('Error: ' + (error.error || 'Failed to deposit funds'));": "showCustomToast('Error: ' + (error.error || 'Failed to deposit funds'), 'error');",
    "alert('Error depositing funds');": "showCustomToast('Error depositing funds', 'error');",

    # 13. Clear Notifications
    "function clearAllNotifications(e) {": "async function clearAllNotifications(e) {",
    'if(!confirm("Are you sure you want to clear all notifications?")) return;':
        'const confirmed = await showCustomConfirm("Are you sure you want to clear all notifications?");\n    if (!confirmed) return;',

    # 14. Approve User
    "if(!confirm('Approve this user?')) return;":
        "const confirmed = await showCustomConfirm('Approve this user?');\n    if (!confirmed) return;",
    "alert('Error: ' + data.error);": "showCustomToast('Error: ' + data.error, 'error');",
    "alert('Failed to approve user');": "showCustomToast('Failed to approve user', 'error');",

    # 15. Reject User
    "if(!confirm('Reject this user and delete account?')) return;":
        "const confirmed = await showCustomConfirm('Reject this user and delete account?');\n    if (!confirmed) return;",
    "alert('Failed to reject user');": "showCustomToast('Failed to reject user', 'error');",

    # 16. Deactivate User
    "alert(\"Failed to deactivate user.\");": "showCustomToast(\"Failed to deactivate user.\", 'error');",
    "alert(\"Error deactivating user.\");": "showCustomToast(\"Error deactivating user.\", 'error');",

    # 17. Reset Password
    'if (!newPassword) return alert("Password cannot be empty.");':
        'if (!newPassword) { showCustomToast("Password cannot be empty.", "info"); return; }',
    'alert("Password reset successfully!");': 'showCustomToast("Password reset successfully!", "success");',
    'alert(data.error || "Failed to reset password");': 'showCustomToast(data.error || "Failed to reset password", "error");',
    'alert("Error resetting password.");': 'showCustomToast("Error resetting password.", "error");'
}

for old, new in replacements.items():
    text = text.replace(old, new)

with open('app/admin_dashboard.html', 'w') as f:
    f.write(text)

