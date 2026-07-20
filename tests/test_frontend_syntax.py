import os
import re
import pytest
import subprocess

def check_node_installed():
    try:
        subprocess.run(["node", "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

# Skip the entire module if Node is not available
pytestmark = pytest.mark.skipif(
    not check_node_installed(),
    reason="Node.js is not installed, skipping frontend syntax checks"
)

def get_html_files():
    app_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app")
    html_files = []
    for root, _, files in os.walk(app_dir):
        for file in files:
            if file.endswith(".html"):
                html_files.append(os.path.join(root, file))
    return html_files

@pytest.mark.parametrize("filepath", get_html_files())
def test_javascript_syntax_in_html(filepath):
    """
    Extracts all inline <script> tags from HTML templates and validates their syntax using node -c.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # Find all inline scripts
    # Note: re.DOTALL makes '.' match newlines
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html_content, re.DOTALL | re.IGNORECASE)

    if not scripts:
        pytest.skip(f"No inline scripts found in {os.path.basename(filepath)}")
        return

    for i, script_content in enumerate(scripts):
        # Ignore external scripts like <script src="..."></script> which have empty inner content
        if not script_content.strip():
            continue

        # Wrap in an async function to gracefully handle top-level await and return statements
        # which are common in modules or inline dashboard scripts.
        js_code = f"async function validation_wrapper_{i}() {{\n{script_content}\n}}"

        try:
            # node -c checks syntax without executing
            p = subprocess.Popen(['node', '-c'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate(input=js_code.encode('utf-8'))
            
            if p.returncode != 0:
                error_msg = err.decode('utf-8')
                pytest.fail(f"Syntax error found in {os.path.basename(filepath)} (Script Block #{i + 1}):\n\n{error_msg}")
        except FileNotFoundError:
            pytest.skip("Node.js not found in system path during execution.")
