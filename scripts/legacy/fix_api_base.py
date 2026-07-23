import re

files = [
    'app/login.html',
    'app/signup.html',
    'app/complete_profile.html',
    'app/user_dashboard.html'
]

new_api_base = """function getApiBase() {
    if (window.location.protocol === 'file:' || 
        (window.location.hostname === '127.0.0.1' && window.location.port !== '8080') || 
        (window.location.hostname === 'localhost' && window.location.port !== '8080')) {
        return 'http://127.0.0.1:8080';
    }
    return '';
}"""

# regex to replace existing function getApiBase() { ... }
pattern = re.compile(r'function getApiBase\(\)\s*\{[^\}]*\}[^\}]*\}', re.MULTILINE)

for filepath in files:
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Simple replace
    # The current one looks like:
    # function getApiBase() {
    #     return (window.location.protocol === 'file:' || window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') 
    #         ? 'https://elitebreakoutsystem-production.up.railway.app' 
    #         : '';
    # }
    
    # Because of regex complexity with nested braces, let's use string split/replace if possible
    # Actually, we can just replace the specific string we injected earlier.
    old_func = """function getApiBase() {
    return (window.location.protocol === 'file:' || window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') 
        ? 'https://elitebreakoutsystem-production.up.railway.app' 
        : '';
}"""
    if old_func in content:
        content = content.replace(old_func, new_api_base)
    else:
        print(f"Could not find exact old function in {filepath}")
        
    with open(filepath, 'w') as f:
        f.write(content)

print("Updated getApiBase in all files")
