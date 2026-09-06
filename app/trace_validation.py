import os, sys, traceback
import pandas as pd
import yfinance as yf

app_dir = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app"
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from validation import ValidationEngine, MarketData, ValidationContext, registry as val_registry, DatasetType

df = yf.download("ADANIPOWER.NS", period="1y", interval="1d", progress=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = [col[0] for col in df.columns]

print(f"Downloaded df shape: {df.shape}, tail date: {df.index[-1]}", flush=True)

pipeline = val_registry.get_pipeline(DatasetType.PRICE)
engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
ctx = ValidationContext(provider="NSE", period="1y", interval="1d", fetch_mode="FULL")
report = engine.validate(df, ctx)

print(f"Validation report is_valid: {report.is_valid}", flush=True)
print(f"Validation quality_score: {report.quality_score}", flush=True)
print(f"Validation status: {report.status}", flush=True)
print(f"Validation checks: {report.checks}", flush=True)
print(f"Validation details: {report.details}", flush=True)
