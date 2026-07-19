# Elite Fixture Rules

To prevent our test suite from degrading into an inconsistent, brittle mess, all fixtures inside `tests/fixtures/` and factories in `tests/factories.py` must strictly adhere to the following rules:

1. **Be Deterministic:** A fixture must produce the exact same object every time it is called. Avoid random generation (`random.random()`, `uuid4()`) unless explicitly testing unpredictability. Hardcode dates and timestamps.
2. **Represent One Business Scenario:** Do not build "god fixtures" that try to represent everything (e.g. `mock_perfect_stock_with_earnings_and_split`). Create focused fixtures for focused scenarios.
3. **Contain Only Minimum Required Data:** If a scanner test only needs `Volume` and `Close`, the fixture should not populate `vwap_30d` or `beta` with arbitrary numbers. Less data means fewer broken tests when unrelated code changes.
4. **Never Call External APIs:** Fixtures must be 100% offline. No exceptions.
5. **Be Reusable:** Construct fixtures via composable factory functions (e.g., `make_candidate().with_volume(2.5)`), allowing other tests to cleanly modify standard templates.
6. **Be Immutable:** A fixture, once returned to a test, should not secretly mutate shared state. Tests that modify fixtures should deepcopy them or use factory builder methods.
7. **Be Documented:** Every base fixture should have a docstring explaining what business scenario it represents.
