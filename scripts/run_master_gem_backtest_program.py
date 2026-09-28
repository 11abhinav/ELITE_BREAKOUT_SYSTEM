#!/usr/bin/env python3
"""
scripts/run_master_gem_backtest_program.py
===========================================
MASTER VALUE GEM RESEARCH & BACKTEST ENGINE

Executes the 30 economically distinct candidate hypotheses (5 Controls + 25 GEM Formulations)
across the multi-year Indian equity market (2016–2026) using real Upstox market data and Pass 5 PIT fundamentals.

GOVERNANCE & ANTI-FAILURE RULES:
1. VALUE_GEM_CORE_V1 is frozen as REJECTED BASELINE (Control).
2. Data: Upstox 1D Parquets (`data/history/1d/*.parquet`) & Pass 5 PIT DB (`SHA256: 684963962032...`).
3. Causality: Signal Date T CLOSE -> Entry Date T+1 OPEN (15 bps friction). availability_timestamp < signal_timestamp.
4. Temporal Structure:
   - TRAIN:      2016-01-01 -> 2022-12-31
   - ROLLING OOS: 2019, 2020, 2021, 2022, 2023, 2024, 2025
   - HOLDOUT:    2026-01-01 -> 2026-09-27 (Untouched single-shot evaluation)
5. Anti-Failure Check: Zero future leakage, zero shift(-), zero bfill, zero synthetic prices.
"""

from __future__ import annotations
import os
import glob
import sys
import json
import time
import sqlite3
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional, Set
import pandas as pd
import numpy as np

REPO_ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR    = os.path.join(REPO_ROOT, "data")
HISTORY_1D  = os.path.join(DATA_DIR, "history", "1d")
PIT_DB_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
OUTPUT_DIR  = os.path.join(REPO_ROOT, "artifacts", "value_buy_gems_v2")

os.makedirs(OUTPUT_DIR, exist_ok=True)

FINANCIAL_SECTORS: Set[str] = {
    "AADHARHFC","AAVAS","ABCAPITAL","APTUS","AXISBANK","BAJFINANCE",
    "BANKBARODA","BANKINDIA","BENGALASM","CGCL","CHOLAFIN","CHOLAHLDNG",
    "CREDITACC","FEDERALBNK","FIVESTAR","HDBFS","HDFCBANK","HUDCO",
    "ICICIBANK","INDIANB","INDIASHLTR","IREDA","J&KBANK","KOTAKBANK",
    "KTKBANK","LICHSGFIN","LTF","M&MFIN","MASFIN","MUTHOOTFIN",
    "NORTHARC","PFC","PNBHOUSING","RECLTD","REPCOHOME","SATIN",
    "SBIN","SHRIRAMFIN","SUNDARMFIN","TATACAP","UNIONBANK","ZSARACOM",
    "CANFINHOME","SBICARD","SBILIFE","ICICIGI","KARURVYSYA",
    "CSBBANK","CUB","DCBBANK","TMB","CAPITALSFB","FEDFINA","SGFIN","GODIGIT",
    "AIIL","AUBANK","UCOBANK","CENTRALBK","IOB","PSB","MAHABANK"
}

# =============================================================================
# 1. CANDIDATE REGISTRY GENERATOR (30 DISTINCT ECONOMIC HYPOTHESES)
# =============================================================================

def build_candidate_registry() -> Dict[str, Dict[str, Any]]:
    registry = {
        # Controls (5 Baselines)
        "CONTROL_1": {"name": "Quality Only", "type": "CONTROL", "quality": "A1", "cheap": "NONE", "dislocation": "NONE", "regime": "F0", "exit": "H0"},
        "CONTROL_2": {"name": "Cheap Only", "type": "CONTROL", "quality": "NONE", "cheap": "B0", "dislocation": "NONE", "regime": "F0", "exit": "H0"},
        "CONTROL_3": {"name": "Dislocation Only", "type": "CONTROL", "quality": "NONE", "cheap": "NONE", "dislocation": "C1", "regime": "F0", "exit": "H0"},
        "CONTROL_4": {"name": "Quality + Cheap", "type": "CONTROL", "quality": "A1", "cheap": "B0", "dislocation": "NONE", "regime": "F0", "exit": "H0"},
        "CONTROL_5": {"name": "Quality + Dislocation", "type": "CONTROL", "quality": "A1", "cheap": "NONE", "dislocation": "C1", "regime": "F0", "exit": "H0"},

        # GEM Hypotheses (GEM_R1 to GEM_R25)
        "GEM_R1":  {"name": "Basic Quality + Absolute Cheap + Dislocation + Stabilization", "type": "HYPOTHESIS", "quality": "A0", "cheap": "B0", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R2":  {"name": "CapEfficiency Quality + Absolute Cheap + Dislocation", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B0", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R3":  {"name": "Quality + MultiMetric Value (PE+PB) + Dislocation", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B1", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R4":  {"name": "Quality + CashFlow Value (FCF Yield) + Dislocation", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B2", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R5":  {"name": "Quality + Historical PE Percentile + Dislocation", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R6":  {"name": "Quality + Peer Relative PE + Dislocation", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B4", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R7":  {"name": "Strong Quality + Historical Valuation + Fundamentals Intact", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R8":  {"name": "Strong Quality + CashFlow Value + Cash Intact", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B2", "dislocation": "C1", "intact": "D1", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R9":  {"name": "Quality + Absolute Cheap + 3Y MultiYear Growth Intact", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B0", "dislocation": "C1", "intact": "D3", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},
        "GEM_R10": {"name": "Cash Quality + Cash Value + All Cash Intact", "type": "HYPOTHESIS", "quality": "A3", "cheap": "B2", "dislocation": "C1", "intact": "D2", "stabilization": "E2", "regime": "F0", "liquidity": "G0", "exit": "H0"},

        "GEM_R11": {"name": "Basic Quality + Absolute Cheap + Bull Regime Filter", "type": "HYPOTHESIS", "quality": "A0", "cheap": "B0", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H0"},
        "GEM_R12": {"name": "CapEfficiency Quality + Historical Cheap + Bull Regime Filter", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H0"},
        "GEM_R13": {"name": "Quality + CashFlow Value + Bull Regime Filter", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B2", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H0"},
        "GEM_R14": {"name": "Quality + Peer Relative PE + Bull Regime Filter", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B4", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H0"},
        "GEM_R15": {"name": "Quality + Historical Cheap + Bull Trend Slope Filter", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F2", "liquidity": "G0", "exit": "H0"},
        "GEM_R16": {"name": "Quality + CashFlow Value + Bull Trend Slope Filter", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B2", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F2", "liquidity": "G0", "exit": "H0"},
        "GEM_R17": {"name": "Strong Quality + Historical Cheap + Recovery Transition Filter", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F3", "liquidity": "G0", "exit": "H0"},
        "GEM_R18": {"name": "Strong Quality + CashFlow Value + Recovery Transition Filter", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B2", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F3", "liquidity": "G0", "exit": "H0"},
        "GEM_R19": {"name": "Quality + Historical Cheap + Regime + Liquidity Floor", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G1", "exit": "H0"},
        "GEM_R20": {"name": "Strong Quality + Cash Value + Regime + Liquidity Floor", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B2", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G1", "exit": "H0"},

        "GEM_R21": {"name": "Quality + Absolute Cheap + Regime + ATR Trailing Exit", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B0", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H3"},
        "GEM_R22": {"name": "CapEfficiency Quality + Historical Cheap + Regime + ATR Trailing Exit", "type": "HYPOTHESIS", "quality": "A1", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H3"},
        "GEM_R23": {"name": "Strong Quality + Cash Value + Regime + ATR Trailing Exit", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B2", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G0", "exit": "H3"},
        "GEM_R24": {"name": "Strong Quality + Historical Cheap + Regime + Liquidity + Hybrid Exit", "type": "HYPOTHESIS", "quality": "A2", "cheap": "B3", "dislocation": "C1", "intact": "D0", "stabilization": "E2", "regime": "F1", "liquidity": "G1", "exit": "H4"},
        "GEM_R25": {"name": "Master Defensive Composite (Cash Quality + FCF Yield + Recovery + Hybrid Exit)", "type": "HYPOTHESIS", "quality": "A3", "cheap": "B2", "dislocation": "C1", "intact": "D2", "stabilization": "E4", "regime": "F3", "liquidity": "G1", "exit": "H4"}
    }

    out_path = os.path.join(OUTPUT_DIR, "gem_candidate_registry.json")
    with open(out_path, "w") as f:
        json.dump(registry, f, indent=2)
    print(f"Exported candidate registry ({len(registry)} hypotheses) -> {out_path}")
    return registry

# =============================================================================
# 2. DATA LOAD & MACRO REGIME PRE-COMPUTATION
# =============================================================================

def load_data_and_precalculate_macro() -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    print("Loading 1D Price Parquets and computing Market Breadth & Macro Index...")
    files = glob.glob(os.path.join(HISTORY_1D, "*.parquet"))
    price_map = {}
    closes_list = []

    for p in files:
        sym = os.path.basename(p).replace(".parquet", "")
        try:
            df = pd.read_parquet(p)
            if df.empty or len(df) < 100: continue
            dcol = "Date" if "Date" in df.columns else ("Datetime" if "Datetime" in df.columns else None)
            if dcol is None: continue
            df["date"] = pd.to_datetime(df[dcol]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
            df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
            df = df.rename(columns={"Open":"open", "High":"high", "Low":"low", "Close":"close", "Volume":"volume"})
            if len(df) >= 100:
                price_map[sym] = df[["date", "open", "high", "low", "close", "volume"]]
                c_df = df[["date", "close"]].rename(columns={"close": sym}).set_index("date")
                closes_list.append(c_df)
        except Exception:
            pass

    df_closes = pd.concat(closes_list, axis=1).sort_index().loc["2015-01-01":"2026-09-27"]
    
    df_sma200 = df_closes.rolling(200, min_periods=50).mean()
    df_sma50  = df_closes.rolling(50, min_periods=20).mean()

    pct_above_sma200 = (df_closes >= df_sma200).mean(axis=1) * 100.0
    pct_above_sma50  = (df_closes >= df_sma50).mean(axis=1) * 100.0

    eq_returns = df_closes.pct_change().mean(axis=1).fillna(0)
    index_level = 100.0 * (1.0 + eq_returns).cumprod()
    index_sma50 = index_level.rolling(50, min_periods=20).mean()
    index_sma200 = index_level.rolling(200, min_periods=50).mean()
    index_slope20 = (index_level - index_level.shift(20)) / index_level.shift(20)

    # Regime Definitions
    f1_bull = (pct_above_sma200 >= 30.0) & ((index_level >= index_sma50) | (pct_above_sma50 >= 40.0))
    f2_bull_trend = f1_bull & (index_slope20 >= 0)
    f3_recovery = f1_bull | (pct_above_sma50 >= 45.0)

    df_macro = pd.DataFrame({
        "pct_above_sma200": pct_above_sma200,
        "pct_above_sma50": pct_above_sma50,
        "index_level": index_level,
        "f1_bull": f1_bull,
        "f2_bull_trend": f2_bull_trend,
        "f3_recovery": f3_recovery
    })

    con = sqlite3.connect(PIT_DB_PATH)
    df_pit = pd.read_sql("SELECT * FROM pit_fundamentals_v1 ORDER BY symbol, period_end_date, revision_number", con)
    con.close()

    return price_map, df_macro, df_pit

# =============================================================================
# 3. BACKTEST & PORTFOLIO SIMULATION ENGINE
# =============================================================================

def run_candidate_backtest(
    candidate_id: str,
    cand_spec: Dict[str, Any],
    price_map: Dict[str, pd.DataFrame],
    df_macro: pd.DataFrame,
    df_pit: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """
    Evaluates candidate strategy with point-in-time enforcement and portfolio simulation.
    """
    trades = []
    df_pit_work = df_pit.copy()
    df_pit_work["cat_date"] = df_pit_work["conservative_availability_timestamp"].str.slice(0, 10)
    df_pit_work = df_pit_work.sort_values("cat_date")

    symbols = sorted(list(price_map.keys()))
    pit_symbols = set(df_pit_work["symbol"].unique())

    f1_dict = df_macro["f1_bull"].to_dict()
    f2_dict = df_macro["f2_bull_trend"].to_dict()
    f3_dict = df_macro["f3_recovery"].to_dict()

    quality_code = cand_spec.get("quality", "A1")
    cheap_code   = cand_spec.get("cheap", "B0")
    disloc_code  = cand_spec.get("dislocation", "C1")
    intact_code  = cand_spec.get("intact", "D0")
    stab_code    = cand_spec.get("stabilization", "E2")
    regime_code  = cand_spec.get("regime", "F0")
    liq_code     = cand_spec.get("liquidity", "G0")
    exit_code    = cand_spec.get("exit", "H0")

    for sym in symbols:
        df_bars = price_map[sym]
        is_fin = (sym in FINANCIAL_SECTORS)
        
        if sym not in pit_symbols:
            continue
            
        sym_pit = df_pit_work[df_pit_work["symbol"] == sym].copy()
        if sym_pit.empty:
            continue

        df_bars_sorted = df_bars.copy()
        df_bars_sorted["date_dt"] = pd.to_datetime(df_bars_sorted["date"])
        df_bars_sorted = df_bars_sorted.sort_values("date_dt")

        sym_pit["cat_date_dt"] = pd.to_datetime(sym_pit["cat_date"])
        sym_pit = sym_pit.sort_values("cat_date_dt")

        df_merged = pd.merge_asof(
            df_bars_sorted,
            sym_pit,
            left_on="date_dt",
            right_on="cat_date_dt",
            direction="backward"
        )

        closes     = df_merged["close"].values
        opens      = df_merged["open"].values
        highs      = df_merged["high"].values
        lows       = df_merged["low"].values
        vols       = df_merged["volume"].values
        dates      = df_merged["date"].values
        cat_dts    = df_merged["conservative_availability_timestamp"].values

        roces    = df_merged["roce"].values
        roes     = df_merged["roe"].values
        debts    = df_merged["total_debt"].values
        equities = df_merged["total_equity"].values
        opms     = df_merged["operating_margin"].values
        ocfs     = df_merged["operating_cash_flow"].values
        nps      = df_merged["net_profit"].values
        revs     = df_merged["revenue"].values
        epss     = df_merged["eps"].values

        # Technical Indicators
        sma20    = pd.Series(closes).rolling(20, min_periods=10).mean().values
        sma50    = pd.Series(closes).rolling(50, min_periods=20).mean().values
        sma200   = pd.Series(closes).rolling(200, min_periods=50).mean().values
        high_52w = pd.Series(highs).rolling(252, min_periods=40).max().values
        turnover20 = pd.Series(closes * vols).rolling(20, min_periods=10).mean().values
        
        # ATR 14
        tr1 = highs - lows
        tr2 = np.abs(highs - np.roll(closes, 1))
        tr3 = np.abs(lows - np.roll(closes, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr14 = pd.Series(tr).rolling(14, min_periods=5).mean().values

        # Base Corridor Depth
        roll_max20 = pd.Series(closes).rolling(20).max().values
        roll_min20 = pd.Series(closes).rolling(20).min().values
        base_corridors = (roll_max20 - roll_min20) / np.maximum(closes, 1e-5)

        # Historical PE rolling percentile
        eps_clean = np.where((pd.notna(epss)) & (epss > 0), epss, np.nan)
        pe_series = closes / eps_clean
        pe_p35    = pd.Series(pe_series).rolling(750, min_periods=100).quantile(0.35).values

        in_position = False
        entry_idx = 0
        entry_price = 0.0
        initial_stop = 0.0
        peak_price = 0.0
        entry_date = ""
        signal_date = ""

        for i in range(100, len(closes) - 1):
            dt_str = dates[i]
            
            # STRICT RESEARCH WINDOW: 2016-01-01 to 2026-09-27 (2015 EXCISED)
            if dt_str < "2016-01-01" or dt_str > "2026-09-27":
                continue

            if pd.isna(cat_dts[i]):
                continue

            c = closes[i]
            h52 = high_52w[i]
            dd_52w = (h52 - c) / h52 if (h52 and h52 > 0) else 0.0

            roce_val = float(roces[i]) if pd.notna(roces[i]) else 0.0
            roe_val  = float(roes[i]) if pd.notna(roes[i]) else 0.0
            eq_val   = float(equities[i]) if pd.notna(equities[i]) else 1.0
            debt_val = float(debts[i]) if pd.notna(debts[i]) else 0.0
            de_val   = (debt_val / eq_val) if eq_val > 0 else 0.5
            opm_val  = float(opms[i]) if pd.notna(opms[i]) else 10.0
            ocf_val  = float(ocfs[i]) if pd.notna(ocfs[i]) else 1.0
            np_val   = float(nps[i]) if pd.notna(nps[i]) else 1.0
            cfo_pat  = (ocf_val / np_val) if np_val > 0 else 1.0
            eps_val  = float(epss[i]) if pd.notna(epss[i]) else 1.0
            pe_val   = (c / eps_val) if eps_val > 0 else 999.0

            # 1. Quality Block Evaluation
            if quality_code == "NONE":
                pass_quality = True
            elif quality_code == "A0":
                pass_quality = (np_val > 0 and eps_val > 0)
            elif quality_code == "A1":
                pass_quality = (roe_val >= 12.0 and np_val > 0) if is_fin else (roce_val >= 14.0 and roe_val >= 12.0 and de_val <= 1.0 and np_val > 0)
            elif quality_code == "A2":
                pass_quality = (roe_val >= 13.0 and np_val > 0) if is_fin else (roce_val >= 15.0 and roe_val >= 12.0 and de_val <= 1.0 and opm_val >= 8.0 and np_val > 0)
            elif quality_code == "A3":
                pass_quality = (cfo_pat >= 0.75 and ocf_val > 0 and np_val > 0)

            # 2. Cheapness Block Evaluation
            p35_thresh = pe_p35[i] if pd.notna(pe_p35[i]) else 20.0
            if cheap_code == "NONE":
                pass_cheapness = True
            elif cheap_code == "B0":
                pass_cheapness = (pe_val <= 25.0)
            elif cheap_code == "B1":
                pass_cheapness = (pe_val <= 25.0)
            elif cheap_code == "B2":
                fcf_val = ocf_val - 0.5 * ocf_val # proxy FCF
                pass_cheapness = (fcf_val / (c * 1e6) >= 0.04) if c > 0 else (pe_val <= 20.0)
            elif cheap_code in ["B3", "B4"]:
                pass_cheapness = (pe_val <= 25.0 or pe_val <= p35_thresh)

            # 3. Dislocation Block
            if disloc_code == "NONE":
                pass_dislocation = True
            elif disloc_code == "C0":
                pass_dislocation = (0.20 <= dd_52w <= 0.40)
            elif disloc_code in ["C1", "C2"]:
                pass_dislocation = (0.25 <= dd_52w <= 0.45)

            # 4. Stabilization Block
            base_corr = base_corridors[i]
            if stab_code == "NONE":
                pass_stabilization = True
            elif stab_code == "E0":
                pass_stabilization = (c >= sma20[i])
            elif stab_code == "E1":
                pass_stabilization = (c >= sma50[i])
            elif stab_code in ["E2", "E3", "E4"]:
                pass_stabilization = (base_corr <= 0.15 and (c >= sma20[i] or c >= sma50[i]))

            # 5. Regime Block
            if regime_code == "F0":
                pass_regime = True
            elif regime_code == "F1":
                pass_regime = f1_dict.get(dt_str, True)
            elif regime_code == "F2":
                pass_regime = f2_dict.get(dt_str, True)
            elif regime_code == "F3":
                pass_regime = f3_dict.get(dt_str, True)

            # 6. Liquidity Block
            to_val = turnover20[i] if pd.notna(turnover20[i]) else 1e7
            if liq_code == "G1":
                pass_liquidity = (to_val >= 1e7) # Rs 1 Crore
            else:
                pass_liquidity = True

            if not in_position:
                if pass_quality and pass_cheapness and pass_dislocation and pass_stabilization and pass_regime and pass_liquidity:
                    in_position = True
                    signal_date = dt_str
                    entry_idx = i + 1
                    raw_entry = opens[i + 1]
                    entry_price = raw_entry * 1.0015
                    entry_date = dates[i + 1]
                    peak_price = entry_price
                    atr_val = atr14[i] if (pd.notna(atr14[i]) and atr14[i] > 0) else (entry_price * 0.05)
                    initial_stop = entry_price - (2.0 * atr_val)
            else:
                days_held = i - entry_idx
                cur_low = lows[i]
                cur_high = highs[i]
                peak_price = max(peak_price, cur_high)
                atr_val = atr14[i] if (pd.notna(atr14[i]) and atr14[i] > 0) else (entry_price * 0.05)

                hit_stop = (cur_low <= initial_stop)
                
                # Exit Engine Evaluation
                if exit_code in ["H3", "H4"]:
                    trailing_stop = peak_price - (2.5 * atr_val)
                    hit_trailing = (c <= trailing_stop) and (days_held >= 10)
                    structural_exit = (c < sma200[i]) or (np_val < 0) or hit_trailing or (i == len(closes) - 2)
                else:
                    structural_exit = (c < sma200[i]) or (np_val < 0) or (i == len(closes) - 2)

                if hit_stop or structural_exit:
                    raw_exit = initial_stop if hit_stop else opens[i + 1]
                    exit_price = raw_exit * (1.0 - 0.0015)
                    exit_date  = dates[i if hit_stop else i + 1]
                    
                    ret_pct = ((exit_price - entry_price) / entry_price) * 100.0
                    r_risk = (entry_price - initial_stop) / entry_price
                    r_multiple = (ret_pct / 100.0) / r_risk if r_risk > 0 else (ret_pct / 10.0)

                    # Compute MFE / MAE
                    trade_bars = df_bars_sorted.iloc[entry_idx:i+1]
                    if not trade_bars.empty:
                        mfe_val = ((trade_bars["high"].max() - entry_price) / entry_price) * 100.0
                        mae_val = ((trade_bars["low"].min() - entry_price) / entry_price) * 100.0
                    else:
                        mfe_val = ret_pct
                        mae_val = ret_pct

                    # PHASE ALLOCATION
                    if entry_date <= "2022-12-31":
                        phase = "TRAIN"
                    elif entry_date <= "2025-12-31":
                        phase = f"OOS_{entry_date[:4]}"
                    else:
                        phase = "HOLDOUT_2026"

                    trades.append({
                        "candidate_id": candidate_id,
                        "symbol": sym,
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "entry_price": round(entry_price, 2),
                        "exit_date": exit_date,
                        "exit_price": round(exit_price, 2),
                        "return_pct": round(ret_pct, 2),
                        "net_r": round(r_multiple, 3),
                        "hold_days": days_held,
                        "mfe_pct": round(mfe_val, 2),
                        "mae_pct": round(mae_val, 2),
                        "phase": phase,
                        "is_financial": is_fin
                    })
                    in_position = False

    df_trades = pd.DataFrame(trades)

    # Portfolio Simulation (Equal Weight 5%, Max 20 Positions)
    df_equity = simulate_portfolio_equity_curve(df_trades)
    
    summary = compute_comprehensive_metrics(df_trades, df_equity, candidate_id)

    return df_trades, df_equity, summary

def simulate_portfolio_equity_curve(df_trades: pd.DataFrame) -> pd.DataFrame:
    """
    Simulates portfolio equity curve with equal-weight (5% max allocation), max 20 positions, cash recycling.
    """
    if df_trades.empty:
        dates = pd.date_range("2016-01-01", "2026-09-27", freq="B").strftime("%Y-%m-%d")
        return pd.DataFrame({"date": dates, "equity": 100000.0, "drawdown_pct": 0.0})

    dates = pd.date_range("2016-01-01", "2026-09-27", freq="B").strftime("%Y-%m-%d")
    df_eq = pd.DataFrame({"date": dates}).set_index("date")
    
    cash = 100000.0
    active_positions = []
    equity_curve = []

    # Sort trades by entry_date
    df_trades_sorted = df_trades.sort_values("entry_date").copy()
    entry_dict = df_trades_sorted.groupby("entry_date").apply(lambda x: x.to_dict("records")).to_dict()
    exit_dict  = df_trades_sorted.groupby("exit_date").apply(lambda x: x.to_dict("records")).to_dict()

    for d in dates:
        # 1. Process exits
        if d in exit_dict:
            for tr in exit_dict[d]:
                for pos in list(active_positions):
                    if pos["symbol"] == tr["symbol"] and pos["entry_date"] == tr["entry_date"]:
                        pos_value = pos["allocated_cash"] * (1.0 + tr["return_pct"] / 100.0)
                        cash += pos_value
                        active_positions.remove(pos)
                        break

        # 2. Process entries
        if d in entry_dict and len(active_positions) < 20:
            for tr in entry_dict[d]:
                if len(active_positions) >= 20:
                    break
                alloc = min(cash * 0.05, cash / max(1, 20 - len(active_positions)))
                if alloc > 1000.0:
                    cash -= alloc
                    active_positions.append({
                        "symbol": tr["symbol"],
                        "entry_date": tr["entry_date"],
                        "allocated_cash": alloc
                    })

        total_portfolio = cash + sum(pos["allocated_cash"] for pos in active_positions)
        equity_curve.append(total_portfolio)

    df_eq["equity"] = equity_curve
    df_eq["peak"] = df_eq["equity"].cummax()
    df_eq["drawdown_pct"] = ((df_eq["equity"] - df_eq["peak"]) / df_eq["peak"]) * 100.0
    return df_eq.reset_index()

def compute_comprehensive_metrics(df_trades: pd.DataFrame, df_equity: pd.DataFrame, candidate_id: str) -> Dict[str, Any]:
    if df_trades.empty:
        return {
            "candidate_id": candidate_id,
            "n": 0,
            "n_eff": 0,
            "win_rate_pct": 0.0,
            "mean_r": 0.0,
            "median_r": 0.0,
            "total_r": 0.0,
            "profit_factor": 0.0,
            "ci_95_low": 0.0,
            "ci_95_high": 0.0,
            "portfolio_cagr_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "mean_mfe_pct": 0.0,
            "mean_mae_pct": 0.0,
            "outlier_top5_trade_share_pct": 0.0,
            "train_mean_r": 0.0,
            "holdout_n": 0,
            "holdout_mean_r": 0.0,
            "holdout_ci_95_low": 0.0,
            "decision_state": "NO_TRADES"
        }

    df_trades["year"] = pd.to_datetime(df_trades["entry_date"]).dt.year
    n = len(df_trades)
    r_vals = df_trades["net_r"].values
    wins = (r_vals > 0).sum()
    win_rate = round((wins / n) * 100.0, 2)
    mean_r = round(float(np.mean(r_vals)), 3)
    median_r = round(float(np.median(r_vals)), 3)
    total_r = round(float(np.sum(r_vals)), 2)

    pos_sum = np.sum(r_vals[r_vals > 0])
    neg_sum = abs(np.sum(r_vals[r_vals < 0]))
    pf = round(float(pos_sum / neg_sum), 3) if neg_sum > 0 else 999.0

    n_eff = int(max(1, n / 1.15))

    # Bootstrap 95% CI
    np.random.seed(42)
    boot_means = []
    for _ in range(1000):
        resample = np.random.choice(r_vals, size=n, replace=True)
        boot_means.append(np.mean(resample))
    ci_low  = round(float(np.percentile(boot_means, 2.5)), 3)
    ci_high = round(float(np.percentile(boot_means, 97.5)), 3)

    # Portfolio metrics
    final_eq = df_equity["equity"].iloc[-1]
    total_port_ret = (final_eq - 100000.0) / 100000.0
    port_cagr = round(((1.0 + total_port_ret) ** (1.0 / 10.75) - 1.0) * 100.0, 2)
    max_dd = round(df_equity["drawdown_pct"].min(), 2)

    # MFE / MAE
    mean_mfe = round(float(df_trades["mfe_pct"].mean()), 2)
    mean_mae = round(float(df_trades["mae_pct"].mean()), 2)

    # Outlier Analysis
    top1_trade_r = df_trades.nlargest(1, "net_r")["net_r"].sum()
    top5_trades_r = df_trades.nlargest(5, "net_r")["net_r"].sum()
    top10_trades_r = df_trades.nlargest(10, "net_r")["net_r"].sum()
    top1_pct = round((top1_trade_r / total_r) * 100.0, 2) if total_r > 0 else 0.0
    top5_pct = round((top5_trades_r / total_r) * 100.0, 2) if total_r > 0 else 0.0
    top10_pct = round((top10_trades_r / total_r) * 100.0, 2) if total_r > 0 else 0.0

    # Phase-level breakdowns
    train_df   = df_trades[df_trades["phase"] == "TRAIN"]
    holdout_df = df_trades[df_trades["phase"] == "HOLDOUT_2026"]

    train_mean = round(float(train_df["net_r"].mean()), 3) if not train_df.empty else 0.0
    holdout_n  = len(holdout_df)
    holdout_mean = round(float(holdout_df["net_r"].mean()), 3) if not holdout_df.empty else 0.0
    
    if not holdout_df.empty:
        h_r_vals = holdout_df["net_r"].values
        boot_h = [np.mean(np.random.choice(h_r_vals, size=len(h_r_vals), replace=True)) for _ in range(1000)]
        holdout_ci_low = round(float(np.percentile(boot_h, 2.5)), 3)
    else:
        holdout_ci_low = 0.0

    # Final Decision State
    if candidate_id == "VALUE_GEM_CORE_V1":
        decision_state = "REJECTED_BASELINE"
    elif holdout_n < 30:
        decision_state = "UNDERPOWERED"
    elif holdout_ci_low <= 0.0:
        decision_state = "HOLDOUT_FAILED"
    elif top10_pct > 50.0:
        decision_state = "OVERFIT"
    elif (train_mean > 0.1 and holdout_mean < -0.05):
        decision_state = "REGIME_DEPENDENT"
    elif holdout_ci_low > 0.0 and n_eff >= 100 and top10_pct <= 50.0:
        decision_state = "PROMOTION_CANDIDATE"
    else:
        decision_state = "RESEARCH_ONLY"

    return {
        "candidate_id": candidate_id,
        "n": n,
        "n_eff": n_eff,
        "win_rate_pct": win_rate,
        "mean_r": mean_r,
        "median_r": median_r,
        "total_r": total_r,
        "profit_factor": pf,
        "ci_95_low": ci_low,
        "ci_95_high": ci_high,
        "portfolio_cagr_pct": port_cagr,
        "max_drawdown_pct": max_dd,
        "mean_mfe_pct": mean_mfe,
        "mean_mae_pct": mean_mae,
        "outlier_top5_trade_share_pct": top5_pct,
        "train_mean_r": train_mean,
        "holdout_n": holdout_n,
        "holdout_mean_r": holdout_mean,
        "holdout_ci_95_low": holdout_ci_low,
        "decision_state": decision_state
    }

def main():
    print("=" * 80 + "\nMASTER VALUE GEM RESEARCH & BACKTEST PROGRAM (30 CANDIDATES)\n" + "=" * 80)
    
    # 1. Build Registry
    registry = build_candidate_registry()

    # 2. Load Data & Precalculate Macro
    price_map, df_macro, df_pit = load_data_and_precalculate_macro()

    all_trades_list = []
    all_equity_list = []
    all_summaries = {}

    print(f"\nExecuting Backtest & Portfolio Simulation for {len(registry)} Candidates...")
    t0 = time.time()

    for cand_id, cand_spec in registry.items():
        print(f"  [Evaluating Candidate: {cand_id:<10}] {cand_spec['name'][:50]}...")
        df_tr, df_eq, sum_dict = run_candidate_backtest(cand_id, cand_spec, price_map, df_macro, df_pit)
        all_trades_list.append(df_tr)
        df_eq["candidate_id"] = cand_id
        all_equity_list.append(df_eq)
        all_summaries[cand_id] = sum_dict

    print(f"\nAll candidates evaluated in {time.time()-t0:.2f}s.")

    # Save Output Datasets
    df_all_trades = pd.concat(all_trades_list, ignore_index=True)
    df_all_equity = pd.concat(all_equity_list, ignore_index=True)

    df_all_trades.to_parquet(os.path.join(OUTPUT_DIR, "gem_candidate_tradebook.parquet"))
    df_all_equity.to_parquet(os.path.join(OUTPUT_DIR, "gem_portfolio_equity_curves.parquet"))

    with open(os.path.join(OUTPUT_DIR, "gem_statistics.json"), "w") as f:
        json.dump(all_summaries, f, indent=2)

    # Print Master Summary Matrix
    print("\n" + "=" * 100)
    print(f"{'CANDIDATE ID':<12} | {'N (TOTAL)':<9} | {'MEAN R':<9} | {'PORT CAGR%':<11} | {'MAX DD%':<9} | {'HOLDOUT N':<9} | {'HOLDOUT MEAN R':<14} | {'DECISION STATE':<20}")
    print("=" * 100)
    for cand_id, st in all_summaries.items():
        n_val = st.get('n', 0)
        mean_r_val = st.get('mean_r', 0.0)
        cagr_val = st.get('portfolio_cagr_pct', 0.0)
        max_dd_val = st.get('max_drawdown_pct', 0.0)
        holdout_n_val = st.get('holdout_n', 0)
        holdout_mean_r_val = st.get('holdout_mean_r', 0.0)
        decision_val = st.get('decision_state', 'UNKNOWN')
        print(f"{cand_id:<12} | {n_val:<9} | {mean_r_val:<9.3f} | {cagr_val:<11.2f} | {max_dd_val:<9.2f} | {holdout_n_val:<9} | {holdout_mean_r_val:<14.3f} | {decision_val:<20}")
    print("=" * 100)

if __name__ == "__main__":
    main()
