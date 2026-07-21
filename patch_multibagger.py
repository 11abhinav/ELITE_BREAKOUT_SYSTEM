import re

with open('app/multibagger.py', 'r') as f:
    content = f.read()

# Remove CONSTITUENT_URLS
content = re.sub(r'# Target URLs for NSE Archives\nCONSTITUENT_URLS = \{.*?\n\}\n', '', content, flags=re.DOTALL)

# Remove HTTP_HEADERS
content = re.sub(r'# Browser-like headers to bypass NSE\'s strict user-agent checking\nHTTP_HEADERS = \{.*?\n\}\n', '', content, flags=re.DOTALL)

# Remove get_nse_session
content = re.sub(r'def get_nse_session\(\):.*?return session\n', '', content, flags=re.DOTALL)

# Remove fetch_constituents
content = re.sub(r'def fetch_constituents\(\) -> list:.*?return sorted\(normalized\)\n', '', content, flags=re.DOTALL)

# Update _start_wrapper to use constituent_service
content = content.replace('symbols = fetch_constituents()', 'from constituent_service import fetch_constituents\n    symbols = fetch_constituents()')

with open('app/multibagger.py', 'w') as f:
    f.write(content)
