import sys
import os

# Add app/ to path so tests can import scanner modules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
