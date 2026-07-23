import re

filepath = 'app/user_dashboard.html'
with open(filepath, 'r') as f:
    content = f.read()

# 1. Add getApiBase() to the first <script> block if not exists
if 'function getApiBase()' not in content:
    api_base_script = """function getApiBase() {
    return (window.location.protocol === 'file:' || window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') 
        ? 'https://elitebreakoutsystem-production.up.railway.app' 
        : '';
}
"""
    content = content.replace('<script>', f'<script>\n  {api_base_script}', 1)

# 2. Update the fetch interceptor to prepend getApiBase() for relative URLs
if 'resource = getApiBase() + resource;' not in content:
    interceptor_old = """    let [resource, config] = args;
    if (config && ['POST', 'PUT', 'DELETE'].includes(config.method)) {"""
    interceptor_new = """    let [resource, config] = args;
    if (typeof resource === 'string' && resource.startsWith('/')) {
        resource = getApiBase() + resource;
    }
    if (config && ['POST', 'PUT', 'DELETE'].includes(config.method)) {"""
    content = content.replace(interceptor_old, interceptor_new)

# 3. Fix initial fetchCsrf call
content = content.replace("fetch('/api/csrf_token')", "fetch(getApiBase() + '/api/csrf_token')")

# 4. Fix getChatApiBase
if 'function getChatApiBase()' in content:
    content = content.replace("window.location.protocol === 'file:'", "(window.location.protocol === 'file:' || window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost')")

with open(filepath, 'w') as f:
    f.write(content)

print("Done user_dashboard")
