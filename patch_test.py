import sys

with open("tests/test_data_quality.py", "r") as f:
    content = f.read()

old_test = """    def test_quality_score_good_data():
        dates = pd.date_range(start="2023-01-01", periods=250, freq="B").tz_localize(IST)
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(100, 200, 250),
            "High": np.random.uniform(200, 250, 250),
            "Low": np.random.uniform(50, 100, 250),
            "Close": np.random.uniform(100, 200, 250),
            "Volume": np.random.randint(1000, 5000, 250)
        })
        report = DataQualityValidator.validate(df, "1y", "1d")
        assert report.is_valid == True
        assert report.quality_score > 90"""

new_test = """    def test_quality_score_good_data():
        dates = pd.date_range(end=pd.Timestamp.now(), periods=250, freq="B").tz_localize(IST)
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(100, 200, 250),
            "High": np.random.uniform(200, 250, 250),
            "Low": np.random.uniform(50, 100, 250),
            "Close": np.random.uniform(100, 200, 250),
            "Volume": np.random.randint(1000, 5000, 250)
        })
        report = DataQualityValidator.validate(df, "1y", "1d")
        assert report.is_valid == True
        assert report.quality_score > 90"""

content = content.replace(old_test, new_test)

with open("tests/test_data_quality.py", "w") as f:
    f.write(content)
