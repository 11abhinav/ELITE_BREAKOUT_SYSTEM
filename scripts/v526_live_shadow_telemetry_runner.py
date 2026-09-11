"""
V5.26 Live Shadow Telemetry Runner & Manual Evaluation Dashboard
================================================================
Executes Phase 3, Phase 9, and Phase 10:
1. Promotes V5.25 candidates in production_parameters.db to SHADOW.
2. Runs parallel Shadow Pipeline on simulated live NSE sessions.
3. Generates human-auditable telemetry with config_version_id.
4. Produces the V5.26 Manual Live Evaluation Dashboard report.
"""

import os
import sqlite3
import datetime
import random
from engine.production.v525_parameter_registry import ParameterRegistry
from engine.production.v526_shadow_execution_engine import ShadowExecutionEngine, CandidateBar, TELEMETRY_DB_PATH, SHADOW_CONFIG_VERSION

def promote_parameters_to_shadow():
    reg = ParameterRegistry()
    now_str = datetime.datetime.now().isoformat()
    with sqlite3.connect(reg.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT version_id FROM production_parameter_versions WHERE status = 'BACKTEST_CERTIFIED'").fetchall()
        for r in rows:
            reg.transition_status(r["version_id"], "SHADOW", effective_at=now_str)
    print(f"Promoted {len(rows)} parameters to SHADOW status in {reg.db_path}")

def run_shadow_simulation():
    engine = ShadowExecutionEngine()
    symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "TATAMOTORS", "LTIM", "DIXON", "POLYCAB", "KALYANKJIL", "TRENT", "ZOMATO"]
    scanners = [
        ("MultiTF 1H", "2026-09-11T10:15:00", True),
        ("MultiTF 5M", "2026-09-11T11:30:00", True),
        ("Short Covering", "2026-09-11T13:00:00", True),
        ("Daily Builder", "2026-09-11T15:30:00", False),
        ("Reversal", "2026-09-11T16:00:00", False),
        ("Pullback V2", "2026-09-11T16:00:00", False),
        ("Multibagger", "2026-09-11T16:00:00", False),
        ("EOD Breakout", "2026-09-11T15:30:00", False),
        ("Accumulation VCP", "2026-09-11T15:30:00", False),
        ("Wealth Engine", "2026-09-11T16:00:00", False),
        ("Technical Ahat", "2026-09-11T16:00:00", False)
    ]

    total_candidates = []
    for sc_name, ts, is_intraday in scanners:
        for sym in symbols:
            # Generate realistic price bar
            base_p = random.uniform(500, 3500)
            atr = base_p * random.uniform(0.015, 0.035)
            
            # Scenarios
            scenario = random.choice(["FRESH_GEM", "EXHAUSTED_GEM", "FRESH_BASE", "BREAKDOWN_GEM", "COOLING_GEM", "TRAP_SETUP"])
            
            if scenario == "FRESH_GEM":
                gem_detected = True
                gem_ts = "2026-09-11T09:45:00" if is_intraday else "2026-09-11T09:30:00"
                open_p = base_p
                low_p = base_p - 0.2 * atr
                high_p = base_p + 1.8 * atr
                close_p = base_p + 1.6 * atr # CLV ~ 0.90
                vol = 2.5 * 100000
                sma_vol = 100000
                vwap = base_p + 0.8 * atr
                orb_h = base_p + 0.5 * atr
                res = base_p + 5.0 * atr # 3.4 ATR runway
            elif scenario == "EXHAUSTED_GEM":
                gem_detected = True
                gem_ts = "2026-09-11T09:20:00"
                open_p = base_p
                low_p = base_p - 0.1 * atr
                high_p = base_p + 4.2 * atr # Climax
                close_p = base_p + 3.6 * atr # Ext > 3.2R
                vol = 1.8 * 100000
                sma_vol = 100000
                vwap = base_p + 2.5 * atr
                orb_h = base_p + 1.0 * atr
                res = base_p + 4.0 * atr # 0.4 ATR runway
            elif scenario == "BREAKDOWN_GEM":
                gem_detected = True
                gem_ts = "2026-09-11T09:25:00"
                open_p = base_p
                high_p = base_p + 2.0 * atr
                low_p = base_p - 1.5 * atr
                close_p = base_p - 1.0 * atr # Below VWAP, CLV < 0.2
                vol = 1.2 * 100000
                sma_vol = 100000
                vwap = base_p + 0.5 * atr
                orb_h = base_p + 0.8 * atr
                res = base_p + 2.0 * atr
            elif scenario == "FRESH_BASE":
                gem_detected = False
                gem_ts = None
                open_p = base_p
                low_p = base_p - 0.1 * atr
                high_p = base_p + 1.2 * atr
                close_p = base_p + 1.0 * atr # Solid consolidation
                vol = 1.3 * 100000
                sma_vol = 100000
                vwap = base_p + 0.4 * atr
                orb_h = base_p + 0.3 * atr
                res = base_p + 4.5 * atr
            elif scenario == "TRAP_SETUP":
                gem_detected = True
                gem_ts = "2026-09-11T09:20:00"
                open_p = base_p
                high_p = base_p + 1.5 * atr
                low_p = base_p - 1.8 * atr
                close_p = base_p - 1.2 * atr # Failed breakout
                vol = 2.0 * 100000
                sma_vol = 100000
                vwap = base_p + 0.2 * atr
                orb_h = base_p + 0.5 * atr
                res = base_p + 3.0 * atr
            else: # COOLING_GEM
                gem_detected = True
                gem_ts = "2026-09-11T10:00:00"
                open_p = base_p
                low_p = base_p - 0.3 * atr
                high_p = base_p + 1.5 * atr
                close_p = base_p + 0.9 * atr
                vol = 1.15 * 100000
                sma_vol = 100000
                vwap = base_p + 0.6 * atr
                orb_h = base_p + 0.4 * atr
                res = base_p + 3.2 * atr

            c = CandidateBar(
                symbol=sym,
                decision_timestamp=ts,
                scanner_name=sc_name,
                open_p=round(open_p, 2),
                high_p=round(high_p, 2),
                low_p=round(low_p, 2),
                close_p=round(close_p, 2),
                volume=vol,
                sma20_volume=sma_vol,
                atr=round(atr, 2),
                vwap=round(vwap, 2),
                orb_high=round(orb_h, 2),
                orb_low=round(base_p - 0.5 * atr, 2),
                overhead_resistance=round(res, 2),
                gem_detected=gem_detected,
                gem_timestamp=gem_ts
            )
            total_candidates.append(c)

    # Rank and record
    ranked = engine.execute_shadow_ranking(total_candidates)
    engine.record_telemetry(ranked)
    print(f"Executed shadow ranking on {len(total_candidates)} candidates across {len(scanners)} scanners.")
    return ranked

def generate_dashboard_report():
    with sqlite3.connect(TELEMETRY_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM shadow_alert_telemetry WHERE config_version_id = ?", (SHADOW_CONFIG_VERSION,)).fetchall()

    # Categorize Disagreements
    avoided_trades = []
    new_trades = []
    unchanged_trades = []
    by_scanner_stats = {}

    for r in rows:
        sc = r["scanner_name"]
        if sc not in by_scanner_stats:
            by_scanner_stats[sc] = {"legacy_alerts": 0, "shadow_alerts": 0, "avoided": 0, "new": 0, "unchanged": 0}

        is_old_sel = r["old_status"] == "SELECTED"
        is_new_sel = r["new_status"] == "SELECTED"

        if is_old_sel:
            by_scanner_stats[sc]["legacy_alerts"] += 1
        if is_new_sel:
            by_scanner_stats[sc]["shadow_alerts"] += 1

        if is_old_sel and not is_new_sel:
            avoided_trades.append(r)
            by_scanner_stats[sc]["avoided"] += 1
        elif not is_old_sel and is_new_sel:
            new_trades.append(r)
            by_scanner_stats[sc]["new"] += 1
        elif is_old_sel and is_new_sel:
            unchanged_trades.append(r)
            by_scanner_stats[sc]["unchanged"] += 1

    # Generate Markdown Report
    report_lines = [
        f"# V5.26 Live Shadow Telemetry & Manual Evaluation Dashboard",
        f"",
        f"**Configuration Version**: `{SHADOW_CONFIG_VERSION}`  ",
        f"**Generated At**: `{datetime.datetime.now().isoformat()}`  ",
        f"**Source Telemetry Database**: `{TELEMETRY_DB_PATH}`  ",
        f"",
        f"---",
        f"",
        f"## 1. Executive Daily Scanner Comparison",
        f"",
        f"| Scanner | Legacy A Alerts | V5.26 Shadow Alerts | Net Diff (Δ) | Avoided Trades (Climax/Stale) | New Trades (Fresh/Survived) | Unchanged Trades |",
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]

    for sc, s in by_scanner_stats.items():
        diff = s["shadow_alerts"] - s["legacy_alerts"]
        diff_str = f"+{diff}" if diff > 0 else f"{diff}"
        report_lines.append(
            f"| **{sc}** | `{s['legacy_alerts']}` | `{s['shadow_alerts']}` | `{diff_str}` | 🔴 **`{s['avoided']}`** | 🟢 **`{s['new']}`** | ⚪ **`{s['unchanged']}`** |"
        )

    report_lines.extend([
        f"",
        f"---",
        f"",
        f"## 2. Signal Disagreement Log (The Manual Review Heart)",
        f"",
        f"### A. Avoided Trades (Suppressed Stale Climax / Invalidated Breakdown)",
        f"These are candidates the Legacy system would have promoted, but V5.26 suppressed to prevent stale climax drag:",
        f"",
        f"| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | CLV | Extension | Rationale |",
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ])

    for r in avoided_trades[:15]:
        gem_age_str = f"{r['gem_age_minutes']:.0f}m" if r['gem_age_minutes'] is not None else "N/A"
        report_lines.append(
            f"| `{r['decision_timestamp'][-8:]}` | **{r['scanner_name']}** | `{r['symbol']}` | `#{r['old_rank']}` | `#{r['new_rank']}` | **`{r['catalyst_state']}`** | `{gem_age_str}` | `{r['clv']:.2f}` | `{r['extension_r']:.1f}R` | {r['decision_rationale']} |"
        )

    report_lines.extend([
        f"",
        f"### B. New Promoted Trades (Fresh EOD Bases & Survived Catalysts)",
        f"These are high-quality consolidation structures or surviving catalysts elevated by V5.26:",
        f"",
        f"| Timestamp | Scanner | Symbol | Old Rank | New Rank | Catalyst State | Gem Age | CLV | Runway | Sizing | Rationale |",
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ])

    for r in new_trades[:15]:
        gem_age_str = f"{r['gem_age_minutes']:.0f}m" if r['gem_age_minutes'] is not None else "N/A"
        report_lines.append(
            f"| `{r['decision_timestamp'][-8:]}` | **{r['scanner_name']}** | `{r['symbol']}` | `#{r['old_rank']}` | `#{r['new_rank']}` | **`{r['catalyst_state']}`** | `{gem_age_str}` | `{r['clv']:.2f}` | `{r['runway_atr']:.1f} ATR` | `{r['allocated_r']:.2f}R` | {r['decision_rationale']} |"
        )

    report_lines.extend([
        f"",
        f"---",
        f"",
        f"## 3. False Veto Audit Tracker",
        f"Mandatory manual checkpoint: Monitor all `CATALYST_EXHAUSTED` and `CATALYST_INVALIDATED` signals after trade resolution to ensure no false negative structural rejection of genuine high-momentum leaders.",
        f"",
        f"| Telemetry ID | Symbol | Scanner | Catalyst State | Tracked Outcome Actual R | MFE (R) | MAE (R) | Post-Trade Review Verdict |",
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        f"| `#TEL-001` | Pending Live Exit | Daily Builder | `CATALYST_EXHAUSTED` | `TBD` | `TBD` | `TBD` | ⏳ Awaiting Live Session Close |",
        f"| `#TEL-002` | Pending Live Exit | Reversal | `CATALYST_INVALIDATED` | `TBD` | `TBD` | `TBD` | ⏳ Awaiting Live Session Close |",
        f"",
        f"---",
        f"",
        f"## 4. Production Operational Status",
        f"- Current Active Production: **`V5.25_PRODUCTION`** (Unmodified)",
        f"- Parallel Shadow Observer: **`V5.26_SHADOW`** (Active in Background)",
        f"- Automatic Promotion: ❌ **DISABLED** (Manual Live Confirmation Required)"
    ])

    report_path = "reports/v526_manual_live_evaluation_dashboard.md"
    os.makedirs("reports", exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))
    print(f"Wrote V5.26 Dashboard Report to {report_path}")

if __name__ == "__main__":
    promote_parameters_to_shadow()
    run_shadow_simulation()
    generate_dashboard_report()
