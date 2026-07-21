import sys
sys.path.append('app')
from memory_profiler import MemoryProfiler
import logging
logging.basicConfig(level=logging.INFO)
with MemoryProfiler("Test"):
    pass
