import re

with open('app/core/multibagger_pipeline.py', 'r') as f:
    content = f.read()

content = re.sub(r'datetime\.now\(\)\.isoformat\(\)', r'datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()', content)
if 'import pytz' not in content:
    content = content.replace('from datetime import datetime', 'from datetime import datetime\nimport pytz')

with open('app/core/multibagger_pipeline.py', 'w') as f:
    f.write(content)
