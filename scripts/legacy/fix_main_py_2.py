with open("app/main.py", "r") as f:
    content = f.read()

content = content.replace(
    '                try:\n                    upsert_scanner_health("MULTIBAGGER_EXIT", status="DOWN"',
    '                if "actively running" not in str(e):\n                    try:\n                        upsert_scanner_health("MULTIBAGGER_EXIT", status="DOWN"'
)

# Fix indentation for the except block too:
content = content.replace(
    ' scheduled_for="Every 15min (market hours)")\n                except Exception:\n                    pass',
    ' scheduled_for="Every 15min (market hours)")\n                    except Exception:\n                        pass'
)

with open("app/main.py", "w") as f:
    f.write(content)
