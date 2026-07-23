import glob

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    if 'function getApiBase()' not in content:
        api_base_script = """function getApiBase() {
    return (window.location.protocol === 'file:' || window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') 
        ? 'https://elitebreakoutsystem-production.up.railway.app' 
        : '';
}
"""
        # Insert at the beginning of the first <script> tag
        content = content.replace('<script>', f'<script>\n  {api_base_script}')

    # Replace specific paths
    replacements = [
        ("fetch('/api/csrf_token')", "fetch(`${getApiBase()}/api/csrf_token`)"),
        ("fetch('/login'", "fetch(`${getApiBase()}/login`"),
        ("fetch('/api/guest_chat'", "fetch(`${getApiBase()}/api/guest_chat`"),
        ("fetch('/api/register'", "fetch(`${getApiBase()}/api/register`"),
        ("fetch('/api/complete_profile'", "fetch(`${getApiBase()}/api/complete_profile`"),
        ("fetch('/api/resend_otp'", "fetch(`${getApiBase()}/api/resend_otp`"),
        ("fetch('/api/verify_otp'", "fetch(`${getApiBase()}/api/verify_otp`"),
        ("window.location.href = '/signup'", "window.location.href = `${getApiBase()}/signup`"),
        ("window.location.href = '/login'", "window.location.href = `${getApiBase()}/login`"),
        ("window.location.href = '/'", "window.location.href = `${getApiBase()}/`")
    ]

    for old, new in replacements:
        content = content.replace(old, new)
        
    with open(filepath, 'w') as f:
        f.write(content)

for file in ['app/login.html', 'app/signup.html', 'app/complete_profile.html']:
    fix_file(file)

print("Done")
