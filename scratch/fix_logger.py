import os
import re

app_dir = os.path.join(os.path.dirname(__file__), '../app')

pattern = re.compile(r'logger\.error\((f?["\'].*?):\s*\{e\}["\']\)')

count = 0
for root, dirs, files in os.walk(app_dir):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r') as f:
                content = f.read()
            
            new_content = re.sub(r'logger\.error\((f?["\'].*?):\s*\{e\}["\']\)', r'logger.exception(\1")', content)
            
            if new_content != content:
                with open(filepath, 'w') as f:
                    f.write(new_content)
                count += 1
                print(f"Updated {filepath}")

print(f"Replaced in {count} files.")
