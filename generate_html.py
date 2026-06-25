import markdown

with open("/Users/abhinavmaheshwari/.gemini/antigravity-ide/brain/ac770885-0b7d-4df9-8244-92f719e86bf8/elite_breakout_system_spec.md", "r") as f:
    md_text = f.read()

# Replace mermaid code blocks with div class="mermaid"
# A simple regex for markdown code blocks
import re
md_text = re.sub(r'```mermaid\n(.*?)\n```', r'<div class="mermaid">\1</div>', md_text, flags=re.DOTALL)

html_content = markdown.markdown(md_text, extensions=['extra', 'tables'])

final_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Elite Breakout System Specification</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        h1, h2, h3 {{ color: #2c3e50; }}
        h1 {{ border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        h2 {{ margin-top: 30px; border-bottom: 1px solid #eee; padding-bottom: 5px; }}
        code {{ background: #f8f9fa; padding: 2px 5px; border-radius: 4px; font-family: Consolas, monospace; }}
        pre {{ background: #f8f9fa; padding: 15px; border-radius: 6px; overflow-x: auto; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        .mermaid {{ margin: 30px 0; display: flex; justify-content: center; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({{startOnLoad:true}});</script>
</head>
<body>
    {html_content}
</body>
</html>
"""

with open("elite_breakout_system_spec.html", "w") as f:
    f.write(final_html)

print("Generated elite_breakout_system_spec.html")
