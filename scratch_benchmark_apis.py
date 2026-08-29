import sys
import os

app_dir = os.path.abspath('app')
print("App dir:", app_dir)
sys.path.insert(0, app_dir)
print("sys.path:", sys.path[:3])

try:
    import dashboard_server
    print("Successfully imported dashboard_server!")
except Exception as e:
    print("Import error:", e)
