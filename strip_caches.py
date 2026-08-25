import re

with open("app/dashboard_server.py", "r") as f:
    content = f.read()

# Remove cache dictionary declarations
content = re.sub(r'_[a-zA-Z0-9_]+_cache\s*=\s*\{[^}]*\}\n?', '', content)
content = re.sub(r'_[a-zA-Z0-9_]+_CACHE\s*=\s*\{[^}]*\}\n?', '', content)
content = re.sub(r'_[a-zA-Z0-9_]+_CACHE\s*=\s*None\n?', '', content)

# Function to remove early return blocks
def remove_early_return(text):
    # Regex for: if _cache... < X.X:\n return Response(...)
    pattern = r'^[ \t]*if\s+_[a-zA-Z0-9_]+(?:_cache|_CACHE)\[.*?\]\s*is\s*not\s*None.*?(?:<|<=)\s*\d+\.\d+:\s*\n[ \t]*return\s+Response\(.*?\)\n'
    return re.sub(pattern, '', text, flags=re.MULTILINE)

content = remove_early_return(content)

# Remove `global _cache_...` declarations
content = re.sub(r'^[ \t]*global\s+_[a-zA-Z0-9_]+(?:_cache|_CACHE)(?:,\s*_[a-zA-Z0-9_]+(?:_cache|_CACHE))*\n', '', content, flags=re.MULTILINE)

# Remove cache assignments
content = re.sub(r'^[ \t]*_[a-zA-Z0-9_]+(?:_cache|_CACHE)\[.*?\]\s*=\s*.*?\n', '', content, flags=re.MULTILINE)

# Add Cache-Control headers to all Response(..., mimetype="application/json")
content = re.sub(
    r'(return\s+Response\([^)]*mimetype="application/json")\)',
    r'\1, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})',
    content
)

# Clean up duplicate headers if any
content = re.sub(
    r', headers=\{"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"\}\s*, headers=\{"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"\}',
    r', headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}',
    content
)

with open("app/dashboard_server.py", "w") as f:
    f.write(content)

print("Done stripping caches!")
