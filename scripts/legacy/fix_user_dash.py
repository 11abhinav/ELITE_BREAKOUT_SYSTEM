import re

filepath = 'app/user_dashboard.html'
with open(filepath, 'r') as f:
    content = f.read()

new_api_base = """function getApiBase() {
    if (window.location.protocol === 'file:' || 
        (window.location.hostname === '127.0.0.1' && window.location.port !== '8080') || 
        (window.location.hostname === 'localhost' && window.location.port !== '8080')) {
        return 'http://127.0.0.1:8080';
    }
    return '';
}"""

content = re.sub(r'function getApiBase\(\)\s*\{[^\}]*\}[^\}]*\}', new_api_base, content, count=1)

with open(filepath, 'w') as f:
    f.write(content)

print("Updated getApiBase in user_dashboard.html")
