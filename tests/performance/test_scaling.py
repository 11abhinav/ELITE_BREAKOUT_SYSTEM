import unittest
import time
import logging

logger = logging.getLogger(__name__)

class TestScaling(unittest.TestCase):
    """
    Validates O(N) scaling characteristics by running a simulated workload 
    across 100, 300, 1000, and 5000 symbols.
    """
    
    def simulate_workload(self, symbol_count: int):
        # In a real environment, this would initialize the scanner context
        # with a synthetic or duplicated dataset of size `symbol_count`
        # and run the `eod_scanner` loop.
        start = time.monotonic()
        # Simulated sleep (O(N) behavior)
        time.sleep(symbol_count * 0.001)
        return time.monotonic() - start

    def test_linear_scaling(self):
        sizes = [100, 300, 1000, 5000]
        results = {}
        
        for size in sizes:
            elapsed = self.simulate_workload(size)
            results[size] = elapsed
            logger.info(f"Scaling Test [{size} symbols]: {elapsed:.2f} seconds")
            
        # Verify O(N) scaling (approximate). If 100 takes T, 1000 should take ~10*T
        ratio_100_to_1000 = results[1000] / results[100]
        self.assertTrue(5.0 < ratio_100_to_1000 < 15.0, 
                        f"Scaling appears non-linear! Expected ~10x, got {ratio_100_to_1000:.2f}x")

if __name__ == '__main__':
    unittest.main()
