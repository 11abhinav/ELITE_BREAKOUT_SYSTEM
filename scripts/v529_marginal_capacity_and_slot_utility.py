"""
V5.29 Research Vector 1: Marginal Capacity & Alert Slot Utility Curve
=====================================================================
Analyzes the performance and incremental marginal value of alert ranks #1 through #5
under Model F across 500 trading days (250 Dev / 250 Holdout) and all market regimes.
"""

import os
import sys
import math
import random
import datetime
import numpy as np
import pandas as pd

# Use same seed protocol for reproducibility
RANDOM_SEED = 529001
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)

SECTORS = ["BANKING", "IT", "AUTO", "PHARMA", "FMCG", "METAL", "REALTY", "ENERGY", "INFRA", "CAPITAL_GOODS"]
NIFTY_REGIMES = ["STRONG_BULL", "NEUTRAL_BULL", "CHOPPY_RANGE", "NEUTRAL_BEAR", "SHARP_SELLOFF"]

def generate_500_day_dataset():
    start_date = datetime.date(2024, 1, 1)
    trading_days = []
    curr = start_date
    while len(trading_days) < 500:
        if curr.weekday() < 5:
            trading_days.append(curr)
        curr += datetime.timedelta(days=1)

    all_candidates = []

    for day_idx, d in enumerate(trading_days):
        # Determine dataset split: Dev (first 250) vs Holdout (last 250)
        split = "DEV" if day_idx < 250 else "HOLDOUT"
        
        regime_weights = [0.25, 0.35, 0.20, 0.15, 0.05]
        nifty_regime = random.choices(NIFTY_REGIMES, weights=regime_weights, k=1)[0]
        
        if nifty_regime == "STRONG_BULL":
            nifty_ret = random.gauss(0.012, 0.004)
            mkt_breadth = random.uniform(0.70, 0.95)
        elif nifty_regime == "NEUTRAL_BULL":
            nifty_ret = random.gauss(0.004, 0.003)
            mkt_breadth = random.uniform(0.55, 0.75)
        elif nifty_regime == "CHOPPY_RANGE":
            nifty_ret = random.gauss(0.000, 0.006)
            mkt_breadth = random.uniform(0.40, 0.60)
        elif nifty_regime == "NEUTRAL_BEAR":
            nifty_ret = random.gauss(-0.005, 0.004)
            mkt_breadth = random.uniform(0.25, 0.45)
        else: # SHARP_SELLOFF
            nifty_ret = random.gauss(-0.018, 0.008)
            mkt_breadth = random.uniform(0.05, 0.25)

        sector_data = {}
        for sec in SECTORS:
            sec_beta = random.uniform(0.7, 1.4)
            sec_alpha = random.gauss(0.0, 0.006)
            sec_ret = nifty_ret * sec_beta + sec_alpha
            sec_breadth = min(1.0, max(0.0, mkt_breadth + random.gauss(0.0, 0.12)))
            sector_data[sec] = {
                "return": sec_ret,
                "breadth": sec_breadth,
                "rs_vs_nifty": sec_ret - nifty_ret
            }

        num_candidates = random.randint(25, 45)
        for c_idx in range(num_candidates):
            sec = random.choice(SECTORS)
            s_data = sector_data[sec]
            
            archetype = random.choices(
                ["PRISTINE_FRESH_BASE", "BREAKOUT_READY_VCP", "COOLING_SURVIVOR", "OVER_EXTENDED_CLIMAX", "WEAK_RETRACEMENT"],
                weights=[0.15, 0.20, 0.25, 0.25, 0.15],
                k=1
            )[0]

            if archetype == "PRISTINE_FRESH_BASE":
                clv = random.uniform(0.80, 0.98)
                extension_r = random.uniform(1.2, 2.2)
                vol_ret = random.uniform(1.3, 2.5)
                runway_atr = random.uniform(3.5, 6.0)
                vwap_rel = "ABOVE_VWAP"
                compression_days = random.randint(12, 35)
                days_since_impulse = random.randint(8, 20)
                dist_to_bo = random.uniform(0.0, 0.4)
                base_tightness = random.uniform(0.6, 1.2)
                close_volume_conc = random.uniform(0.35, 0.60)
                is_winner_bias = 0.85
            elif archetype == "BREAKOUT_READY_VCP":
                clv = random.uniform(0.75, 0.92)
                extension_r = random.uniform(1.5, 2.4)
                vol_ret = random.uniform(1.2, 2.0)
                runway_atr = random.uniform(3.0, 5.0)
                vwap_rel = "ABOVE_VWAP"
                compression_days = random.randint(15, 45)
                days_since_impulse = random.randint(5, 15)
                dist_to_bo = random.uniform(0.1, 0.8)
                base_tightness = random.uniform(0.8, 1.4)
                close_volume_conc = random.uniform(0.30, 0.50)
                is_winner_bias = 0.78
            elif archetype == "COOLING_SURVIVOR":
                clv = random.uniform(0.68, 0.82)
                extension_r = random.uniform(2.2, 2.9)
                vol_ret = random.uniform(1.0, 1.6)
                runway_atr = random.uniform(2.5, 4.0)
                vwap_rel = "ABOVE_VWAP" if random.random() > 0.15 else "BELOW_VWAP"
                compression_days = random.randint(5, 15)
                days_since_impulse = random.randint(3, 8)
                dist_to_bo = random.uniform(0.5, 1.5)
                base_tightness = random.uniform(1.2, 2.0)
                close_volume_conc = random.uniform(0.20, 0.40)
                is_winner_bias = 0.60
            elif archetype == "OVER_EXTENDED_CLIMAX":
                clv = random.uniform(0.55, 0.78)
                extension_r = random.uniform(3.2, 5.5)
                vol_ret = random.uniform(1.8, 4.5)
                runway_atr = random.uniform(0.5, 2.2)
                vwap_rel = "ABOVE_VWAP" if random.random() > 0.30 else "BELOW_VWAP"
                compression_days = random.randint(1, 4)
                days_since_impulse = random.randint(0, 2)
                dist_to_bo = random.uniform(2.0, 4.5)
                base_tightness = random.uniform(2.2, 4.5)
                close_volume_conc = random.uniform(0.15, 0.35)
                is_winner_bias = 0.32
            else: # WEAK_RETRACEMENT
                clv = random.uniform(0.35, 0.64)
                extension_r = random.uniform(1.8, 3.2)
                vol_ret = random.uniform(0.6, 1.1)
                runway_atr = random.uniform(1.0, 2.8)
                vwap_rel = "BELOW_VWAP" if random.random() > 0.25 else "ABOVE_VWAP"
                compression_days = random.randint(2, 8)
                days_since_impulse = random.randint(2, 6)
                dist_to_bo = random.uniform(1.0, 2.5)
                base_tightness = random.uniform(1.8, 3.0)
                close_volume_conc = random.uniform(0.10, 0.25)
                is_winner_bias = 0.22

            stock_ret = s_data["return"] + random.gauss(0.005, 0.012)
            rs_vs_sector = stock_ret - s_data["return"]
            rs_vs_nifty = stock_ret - nifty_ret

            if dist_to_bo <= 0.5 and base_tightness <= 1.5 and runway_atr >= 3.0:
                readiness_score = 90.0
            elif dist_to_bo <= 1.2 and base_tightness <= 2.2 and runway_atr >= 2.2:
                readiness_score = 70.0
            elif extension_r > 3.0 or dist_to_bo > 2.0:
                readiness_score = 25.0
            else:
                readiness_score = 40.0

            fresh_score = min(100.0, max(0.0, (
                min(compression_days / 20.0, 1.0) * 35.0 +
                max(0.0, 1.0 - (days_since_impulse / 15.0)) * 25.0 +
                max(0.0, 1.0 - (base_tightness / 3.0)) * 25.0 +
                (15.0 if vol_ret >= 1.2 else 5.0)
            )))

            p_ext = max(0.0, (extension_r - 2.20) * 18.0)
            p_wick = max(0.0, (1.0 - clv) * 25.0)
            p_runway = max(0.0, (3.0 - runway_atr) * 12.0)
            exhaustion_penalty = min(80.0, p_ext + p_wick + p_runway)

            s_base = min(compression_days / 15.0, 1.0) * 20.0
            s_clv = clv * 30.0
            s_run = min(runway_atr / 4.0, 1.0) * 25.0
            s_vol = min(vol_ret / 1.5, 1.0) * 25.0
            structure_score = s_base + s_clv + s_run + s_vol

            t_bo = (readiness_score / 100.0) * 35.0
            t_fresh = (fresh_score / 100.0) * 30.0
            t_vwap = 20.0 if vwap_rel == "ABOVE_VWAP" else 0.0
            t_vol_conc = min(close_volume_conc / 0.4, 1.0) * 15.0
            timing_score = t_bo + t_fresh + t_vwap + t_vol_conc

            sec_score = 50.0
            if s_data["rs_vs_nifty"] > 0: sec_score += 15.0
            if s_data["breadth"] > 0.65: sec_score += 15.0
            if rs_vs_sector > 0: sec_score += 20.0
            sec_score = min(100.0, max(0.0, sec_score))

            scanner_triggers = ["Daily Builder"]
            if archetype in ["BREAKOUT_READY_VCP", "PRISTINE_FRESH_BASE"] and rs_vs_nifty > 0.01:
                scanner_triggers.append("Multibagger")
            if archetype == "PRISTINE_FRESH_BASE" and compression_days >= 20:
                scanner_triggers.append("Pullback V2")
            if archetype == "COOLING_SURVIVOR" and clv >= 0.85:
                scanner_triggers.append("Reversal")

            multi_scanner_count = len(scanner_triggers)
            exhaust_dampener = 1.0 / (1.0 + math.exp((exhaustion_penalty - 20.0) / 6.0))

            mkt_factor = 1.0
            if nifty_regime == "STRONG_BULL": mkt_factor = 1.15
            elif nifty_regime == "NEUTRAL_BULL": mkt_factor = 1.05
            elif nifty_regime == "CHOPPY_RANGE": mkt_factor = 0.90
            elif nifty_regime == "NEUTRAL_BEAR": mkt_factor = 0.70
            else: mkt_factor = 0.40

            sec_factor = (sec_score / 100.0) * 0.30 + 0.70
            multi_bonus = (multi_scanner_count - 1) * 6.0
            
            raw_f = ((structure_score * 0.45 + timing_score * 0.45 + multi_bonus) * exhaust_dampener * sec_factor * mkt_factor)
            if vwap_rel == "BELOW_VWAP" or clv < 0.55 or extension_r > 3.20:
                raw_f = 0.0
            model_f_score = max(0.0, raw_f)

            win_prob = is_winner_bias * 0.55 + (sec_score / 100.0) * 0.20 + (1.0 if nifty_regime in ["STRONG_BULL", "NEUTRAL_BULL"] else 0.5 if nifty_regime == "CHOPPY_RANGE" else 0.2) * 0.25
            win_prob = max(0.10, min(0.92, win_prob))
            is_win = random.random() < win_prob

            if is_win:
                realized_r = random.gauss(1.65, 0.55)
                realized_r = max(0.20, min(4.80, realized_r))
                mfe = realized_r + random.uniform(0.4, 1.8)
                mae = -random.uniform(0.10, 0.55)
            else:
                realized_r = random.gauss(-0.75, 0.30)
                realized_r = min(-0.10, max(-1.00, realized_r))
                mfe = random.uniform(0.1, 0.6)
                mae = -random.uniform(0.65, 1.00)

            all_candidates.append({
                "date": d.isoformat(),
                "day_idx": day_idx,
                "split": split,
                "symbol": f"{sec}_{c_idx+1}",
                "sector": sec,
                "nifty_regime": nifty_regime,
                "archetype": archetype,
                "clv": clv,
                "extension_r": extension_r,
                "vol_ret": vol_ret,
                "runway_atr": runway_atr,
                "vwap_rel": vwap_rel,
                "compression_days": compression_days,
                "days_since_impulse": days_since_impulse,
                "dist_to_bo": dist_to_bo,
                "base_tightness": base_tightness,
                "fresh_score": fresh_score,
                "exhaustion_penalty": exhaustion_penalty,
                "structure_score": structure_score,
                "timing_score": timing_score,
                "sec_score": sec_score,
                "model_f_score": model_f_score,
                "is_win": is_win,
                "realized_r": realized_r,
                "mfe": mfe,
                "mae": mae
            })

    return pd.DataFrame(all_candidates)

def analyze_slot_utility(df):
    print("\n" + "=" * 80)
    print("V5.29 RESEARCH: ALERT SLOT UTILITY & MARGINAL CAPACITY CURVE (0 to 5)")
    print("=" * 80)

    # Filter to qualified candidates under Model F (Score >= 58.0, Exhaustion <= 25.0, Selloff = 0)
    dev_df = df[df["split"] == "DEV"].copy()
    
    # Process day by day to rank and assign slots
    dev_results = []
    
    for (day_idx, date, regime), group in dev_df.groupby(["day_idx", "date", "nifty_regime"]):
        if regime == "SHARP_SELLOFF":
            continue # Complete shutdown
        
        # Qualified candidates
        qualified = group[(group["model_f_score"] >= 58.0) & (group["exhaustion_penalty"] <= 25.0)].copy()
        if len(qualified) == 0:
            continue
        
        # Sort descending by model_f_score
        qualified = qualified.sort_values(by="model_f_score", ascending=False).reset_index(drop=True)
        
        # Take up to top 5
        top5 = qualified.head(5).copy()
        for slot_idx, row in top5.iterrows():
            dev_results.append({
                "day_idx": day_idx,
                "date": date,
                "regime": regime,
                "slot": slot_idx + 1, # 1 to 5
                "model_f_score": row["model_f_score"],
                "realized_r": row["realized_r"],
                "is_win": row["is_win"],
                "mfe": row["mfe"],
                "mae": row["mae"]
            })

    slot_df = pd.DataFrame(dev_results)
    
    # Aggregate by Slot across all regimes (Dev Split)
    print("\n--- OVERALL MARGINAL METRICS BY ALERT SLOT (DEV SET: 250 DAYS) ---")
    summary = []
    for slot in range(1, 6):
        s_data = slot_df[slot_df["slot"] == slot]
        n = len(s_data)
        if n == 0:
            continue
        wins = s_data["is_win"].sum()
        wr = (wins / n) * 100.0
        tot_r = s_data["realized_r"].sum()
        mean_r = s_data["realized_r"].mean()
        win_r = s_data[s_data["realized_r"] > 0]["realized_r"].sum()
        loss_r = abs(s_data[s_data["realized_r"] < 0]["realized_r"].sum())
        pf = (win_r / loss_r) if loss_r > 0 else 99.0
        mfe = s_data["mfe"].mean()
        mae = s_data["mae"].mean()
        
        summary.append({
            "Slot": f"Alert #{slot}",
            "N": n,
            "Total R": round(tot_r, 2),
            "Win Rate (%)": round(wr, 2),
            "E[R] (Mean)": round(mean_r, 3),
            "Profit Factor": round(pf, 2),
            "Avg MFE": round(mfe, 2),
            "Avg MAE": round(mae, 2)
        })
    
    sum_df = pd.DataFrame(summary)
    print(sum_df.to_markdown(index=False))

    # Breakdown by Regime and Slot
    print("\n--- REGIME-CONDITIONED MARGINAL UTILITY MATRIX ---")
    regime_slot_summary = []
    for reg in ["STRONG_BULL", "NEUTRAL_BULL", "CHOPPY_RANGE", "NEUTRAL_BEAR"]:
        for slot in range(1, 6):
            s_data = slot_df[(slot_df["regime"] == reg) & (slot_df["slot"] == slot)]
            n = len(s_data)
            if n == 0:
                continue
            wr = (s_data["is_win"].sum() / n) * 100.0
            mean_r = s_data["realized_r"].mean()
            tot_r = s_data["realized_r"].sum()
            win_r = s_data[s_data["realized_r"] > 0]["realized_r"].sum()
            loss_r = abs(s_data[s_data["realized_r"] < 0]["realized_r"].sum())
            pf = (win_r / loss_r) if loss_r > 0 else 99.0
            
            regime_slot_summary.append({
                "Regime": reg,
                "Slot": f"Alert #{slot}",
                "N": n,
                "Win Rate (%)": round(wr, 1),
                "Mean E[R]": round(mean_r, 3),
                "Total R": round(tot_r, 2),
                "Profit Factor": round(pf, 2)
            })
            
    reg_df = pd.DataFrame(regime_slot_summary)
    print(reg_df.to_markdown(index=False))
    
    return df, slot_df

if __name__ == "__main__":
    df = generate_500_day_dataset()
    df, slot_df = analyze_slot_utility(df)
    df.to_parquet("data/v529_500_day_research_dataset.parquet")
    print("\nSaved full 500-day research dataset to data/v529_500_day_research_dataset.parquet")
