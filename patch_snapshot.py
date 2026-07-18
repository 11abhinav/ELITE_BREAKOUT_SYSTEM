with open("tests/test_v7_snapshot.py", "r") as f:
    code = f.read()

code = code.replace('"entry": 100.0,', '"entry_price": 100.0, "entry": 100.0, "candle_range": 3.0,')
code = code.replace('scanner=scanner_name, engine_version="v7.0", **context', 'mode=scanner_name, engine_version="v7.0", **context')

with open("tests/test_v7_snapshot.py", "w") as f:
    f.write(code)
