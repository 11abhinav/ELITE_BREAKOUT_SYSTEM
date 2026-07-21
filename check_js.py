import sys, re
from subprocess import Popen, PIPE

def extract_and_check(filepath):
    with open(filepath, 'r') as f:
        html = f.read()
    
    scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    if not scripts:
        print("No script tags found.")
        return

    for i, script in enumerate(scripts):
        # We will wrap it in an async function to avoid top-level await errors, but keep syntax parsing
        js_code = f"async function test_{i}() {{\n{script}\n}}"
        p = Popen(['node', '-c'], stdin=PIPE, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate(input=js_code.encode('utf-8'))
        if p.returncode != 0:
            print(f"Error in script {i}:")
            print(err.decode('utf-8'))
            return
    print("No syntax errors found!")

extract_and_check('app/user_dashboard.html')
