"""
v5.1.2 Live / Paper Forward Monitoring & Governance Pipeline
Engineered to record, resolve, audit, and track real-time/shadow forward outcomes across all 7 scanners.

Key Enhancements:
  1. Explicit LIVE_FORWARD_OOS Audit Counters (received, rejected, valid, pending, resolved, censored, DQ failures).
  2. Live PULLBACK Paired Delta Tracking: Delta_Net_R = Net_R(v5.1.2 ATR) - Net_R(v5.1.1 Fixed 4%).
  3. Frozen Governance Matrix Enforcing N >= 100 OOS threshold before any v5.1.3 statistical review.
"""

import os
import sys
import json
import zoneinfo
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../app")))

from engine.analytics.quality_contract import ScannerType
from engine.analytics.scanner_quality_runtime import score_scanner_alert
from engine.analytics.pullback_geometry import calculate_pullback_sl_target
from engine.analytics.forward_outcome_resolver import (
    resolve_trade_path,
    SCANNER_EXECUTION_POLICIES,
    ObservationState
)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
FORWARD_LEDGER_PATH = "artifacts/telemetry/v512_live_forward_ledger.jsonl"
MONITORING_REPORT_PATH = "artifacts/reports/v512_live_forward_monitoring_report.md"


class LiveForwardMonitoringEngine:
    """
    Manages live/paper shadow alert ingestion, outcome resolution,
    governance metrics accumulation, and statistical threshold gating.
    """
    def __init__(self, ledger_path: str = FORWARD_LEDGER_PATH):
        self.ledger_path = ledger_path
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)

    def record_alert(
        self,
        alert_id: str,
        scanner: ScannerType,
        symbol: str,
        timestamp: datetime,
        entry_price: float,
        features: Dict[str, Any],
        atr_14: Optional[float] = None,
        df_future_bars: Optional[pd.DataFrame] = None
    ) -> Dict[str, Any]:
        """
        Processes a real or shadow alert through canonical v5.1.2 quality scoring,
        calculates geometry, and resolves forward outcome for both active and shadow models.
        """
        # 1. Quality Scoring
        try:
            score, tier, action, meta = score_scanner_alert(scanner, features)
            is_valid_quality = True
            dq_failure_reason = None
        except Exception as e:
            score, tier, action, meta = 0.0, "SUBPAR", "REJECT", {"model_id": "ERROR"}
            is_valid_quality = False
            dq_failure_reason = str(e)

        # 2. Geometry Calculation
        shadow_base_sl = None
        shadow_base_target = None
        shadow_net_r = None
        shadow_outcome_str = None

        if scanner == ScannerType.PULLBACK:
            geom = calculate_pullback_sl_target(entry_price, atr_14)
            stop_loss = geom["stop_loss"]
            target_price = geom["target_price"]
            actual_risk = geom["actual_risk"]
            clamped_stop_pct = geom["clamped_stop_pct"]
            geometry_type = "v5.1.2_ADAPTIVE_ATR"
            
            # Shadow baseline calculation for PULLBACK (v5.1.1 Fixed 4.0% SL, 2.5R Target)
            shadow_base_sl = round(entry_price * 0.96, 2)
            shadow_base_risk = round(entry_price - shadow_base_sl, 4)
            shadow_base_target = round(entry_price + (2.5 * shadow_base_risk), 2)
        elif scanner == ScannerType.MULTIBAGGER:
            stop_loss = round(entry_price * 0.94, 2)
            actual_risk = round(entry_price - stop_loss, 4)
            target_price = round(entry_price + (3.0 * actual_risk), 2)
            clamped_stop_pct = 0.060
            geometry_type = "v5.1.1_BASE_ACCUMULATION"
        else:
            stop_loss = round(entry_price * 0.95, 2)
            actual_risk = round(entry_price - stop_loss, 4)
            target_price = round(entry_price + (2.0 * actual_risk), 2)
            clamped_stop_pct = 0.050
            geometry_type = "STANDARD_STRUCTURAL"

        # 3. Forward Outcome Resolution (if future bars available)
        alert_payload_active = {
            "alert_id": alert_id,
            "scanner": scanner.value,
            "symbol": symbol,
            "entry_price": entry_price,
            "stop_price": stop_loss,
            "target_price": target_price,
            "is_short": False
        }

        if is_valid_quality and df_future_bars is not None and not df_future_bars.empty:
            df_norm = df_future_bars.copy()
            rename_map = {c: c.capitalize() for c in df_norm.columns if c.lower() in ["open", "high", "low", "close", "volume"]}
            df_norm = df_norm.rename(columns=rename_map)

            # Active v5.1.2 Outcome Resolution
            outcome = resolve_trade_path(
                alert=alert_payload_active,
                df_future_bars=df_norm,
                scanner_type=scanner,
                decision_timestamp=timestamp,
                observation_complete=True
            )
            obs_state = outcome.get("observation_state", "PENDING")
            exit_price = outcome.get("exit_price")
            exit_ts = str(outcome.get("exit_timestamp")) if outcome.get("exit_timestamp") else None
            gross_r = outcome.get("gross_realized_R")
            frict_r = outcome.get("friction_drag_R")
            net_r = outcome.get("net_realized_R")
            bars_held = outcome.get("bars_held", 0)
            outcome_str = outcome.get("exit_reason", "RESOLVED")

            # Parallel Shadow v5.1.1 Baseline Outcome Resolution (PULLBACK)
            if scanner == ScannerType.PULLBACK and shadow_base_sl is not None:
                alert_payload_shadow = {
                    "alert_id": alert_id + "_SHADOW",
                    "scanner": scanner.value,
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_price": shadow_base_sl,
                    "target_price": shadow_base_target,
                    "is_short": False
                }
                outcome_shadow = resolve_trade_path(
                    alert=alert_payload_shadow,
                    df_future_bars=df_norm,
                    scanner_type=scanner,
                    decision_timestamp=timestamp,
                    observation_complete=True
                )
                shadow_net_r = outcome_shadow.get("net_realized_R")
                shadow_outcome_str = outcome_shadow.get("exit_reason", "RESOLVED")

        else:
            obs_state = ObservationState.PENDING.value if is_valid_quality else ObservationState.CENSORED.value
            exit_price = None
            exit_ts = None
            gross_r = None
            frict_r = None
            net_r = None
            bars_held = 0
            outcome_str = "PENDING" if is_valid_quality else "REJECTED_DQ"

        # Calculate live paired delta if resolved
        if net_r is not None and shadow_net_r is not None:
            delta_net_r = round(net_r - shadow_net_r, 4)
        else:
            delta_net_r = None

        record = {
            "alert_id": alert_id,
            "scanner": scanner.value,
            "symbol": symbol,
            "decision_timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S IST"),
            "model_id": meta.get("model_id"),
            "aqs_score": score,
            "quality_tier": tier,
            "action": action,
            "is_valid_quality": is_valid_quality,
            "dq_failure_reason": dq_failure_reason,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "actual_risk": actual_risk,
            "clamped_stop_pct": clamped_stop_pct,
            "atr_14": atr_14,
            "geometry_type": geometry_type,
            "shadow_baseline_sl": shadow_base_sl,
            "shadow_baseline_target": shadow_base_target,
            "shadow_net_r": shadow_net_r,
            "shadow_outcome": shadow_outcome_str,
            "delta_net_r": delta_net_r,
            "outcome": outcome_str,
            "observation_state": obs_state,
            "exit_price": exit_price,
            "exit_timestamp": exit_ts,
            "bars_held": bars_held,
            "gross_r": gross_r,
            "friction_r": frict_r,
            "net_r": net_r,
            "partition": "LIVE_FORWARD_OOS"
        }

        # Append to immutable ledger
        with open(self.ledger_path, "a") as f:
            f.write(json.dumps(record) + "\n")

        return record

    def generate_governance_report(self, report_path: str = MONITORING_REPORT_PATH) -> str:
        """
        Aggregates live forward monitoring ledger records and generates
        a periodic scanner governance report with explicit LIVE_FORWARD_OOS counters.
        """
        if not os.path.exists(self.ledger_path):
            records = []
        else:
            records = []
            with open(self.ledger_path, "r") as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))

        df = pd.DataFrame(records) if records else pd.DataFrame()

        # 1. LIVE_FORWARD_OOS Telemetry Audit Counters
        if not df.empty and "observation_state" in df.columns:
            total_received = len(df)
            total_rejected = len(df[df["action"] == "REJECT"]) if "action" in df.columns else 0
            dq_failures = len(df[df["is_valid_quality"] == False]) if "is_valid_quality" in df.columns else 0
            valid_forward = total_received - total_rejected - dq_failures
            pending_count = len(df[df["observation_state"].isin(["PENDING", "PARTIALLY_OBSERVED_PENDING"])])
            resolved_count = len(df[df["observation_state"].isin(["RESOLVED", "RESOLVED_TIME_HORIZON"])])
            censored_count = len(df[df["observation_state"] == "CENSORED"])
        else:
            total_received = 0
            total_rejected = 0
            dq_failures = 0
            valid_forward = 0
            pending_count = 0
            resolved_count = 0
            censored_count = 0

        # 2. Scanner-Specific Performance Rows
        scanners = [
            ScannerType.PULLBACK.value,
            ScannerType.MULTIBAGGER.value,
            ScannerType.WEALTH_ENGINE.value,
            ScannerType.EOD.value,
            ScannerType.DAILY_BUILDER.value,
            ScannerType.MULTI_TF.value,
            ScannerType.REVERSAL.value
        ]

        rows = []
        for sc in scanners:
            if not df.empty and "scanner" in df.columns:
                sub = df[df["scanner"] == sc]
                n_total = len(sub)
                sub_res = sub[sub["observation_state"].isin(["RESOLVED", "RESOLVED_TIME_HORIZON"])]
                n_resolved = len(sub_res)
                if n_resolved > 0:
                    net_r_vals = sub_res["net_r"].dropna().values
                    mean_net_r = f"{np.mean(net_r_vals):+.3f}R" if len(net_r_vals) > 0 else "—"
                    wins = net_r_vals[net_r_vals > 0]
                    losses = net_r_vals[net_r_vals < 0]
                    win_rate = f"{(len(wins)/len(net_r_vals))*100:.1f}%" if len(net_r_vals) > 0 else "—"
                    pf = f"{np.sum(wins)/np.abs(np.sum(losses)):.2f}" if len(losses) > 0 and np.sum(losses) != 0 else "—"
                else:
                    mean_net_r = "—"
                    win_rate = "—"
                    pf = "—"
            else:
                n_total = 0
                n_resolved = 0
                mean_net_r = "—"
                win_rate = "—"
                pf = "—"

            # Governance Action Gating
            if sc in [ScannerType.PULLBACK.value, ScannerType.MULTIBAGGER.value]:
                gov_status = "🟢 ACTIVE FORWARD MONITORING"
                action_req = "Accumulate live forward outcomes; preserve frozen logic"
            elif sc == ScannerType.WEALTH_ENGINE.value:
                gov_status = "🟢 ACTIVE PORTFOLIO MONITORING"
                action_req = "Track monthly CAGR and drawdown relative to benchmark"
            else:
                if n_resolved < 100:
                    gov_status = f"🟡 ACCUMULATING OOS ({n_resolved}/100)"
                    action_req = "HOLD FROZEN (Zero optimization allowed until N >= 100)"
                else:
                    gov_status = "🔵 READY FOR STATISTICAL REVIEW"
                    action_req = "Evaluate failure anatomy for candidate improvements"

            rows.append({
                "Scanner Engine": f"**`{sc}`**",
                "Live Received": n_total,
                "Resolved (N)": n_resolved,
                "Win Rate %": win_rate,
                "Mean Net R": mean_net_r,
                "Net PF": pf,
                "Governance Status": gov_status,
                "Prescribed Operational Action": action_req
            })

        df_gov = pd.DataFrame(rows)

        # 3. Live PULLBACK vs Shadow v5.1.1 Paired Delta Audit
        if not df.empty and "scanner" in df.columns and "delta_net_r" in df.columns:
            pb_sub = df[(df["scanner"] == "PULLBACK") & (df["observation_state"].isin(["RESOLVED", "RESOLVED_TIME_HORIZON"])) & (df["delta_net_r"].notna())]
            n_pb_paired = len(pb_sub)
            if n_pb_paired > 0:
                pb_deltas = pb_sub["delta_net_r"].values
                mean_pb_delta = f"{np.mean(pb_deltas):+.3f}R"
                median_pb_delta = f"{np.median(pb_deltas):+.3f}R"
                pct_pb_improved = f"{(np.sum(pb_deltas > 0)/n_pb_paired)*100:.1f}%"
                pct_pb_worsened = f"{(np.sum(pb_deltas < 0)/n_pb_paired)*100:.1f}%"
            else:
                mean_pb_delta = "—"
                median_pb_delta = "—"
                pct_pb_improved = "—"
                pct_pb_worsened = "—"
        else:
            n_pb_paired = 0
            mean_pb_delta = "—"
            median_pb_delta = "—"
            pct_pb_improved = "—"
            pct_pb_worsened = "—"

        def df_to_markdown(d: pd.DataFrame) -> str:
            headers = [str(c) for c in d.columns]
            header_line = "| " + " | ".join(headers) + " |"
            sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
            data_lines = []
            for _, r in d.iterrows():
                row_str = "| " + " | ".join(str(val) for val in r.values) + " |"
                data_lines.append(row_str)
            return "\n".join([header_line, sep_line] + data_lines)

        report_content = f"""# v5.1.2 Live / Paper Forward Monitoring & Governance Dashboard

**Generated Date:** {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}  
**Active Release:** **v5.1.2 (FROZEN)**  
**Authoritative Quality Registry:** `engine/analytics/scanner_quality_runtime.py`  
**Authoritative Pullback Geometry:** `engine/analytics/pullback_geometry.py`  
**Transaction Friction Contract:** Strict $4$-Component ($0.0005(E+X)$)  

---

## 1. LIVE_FORWARD_OOS Telemetry Audit Counters

```
LIVE_FORWARD_OOS Telemetry Lifecycle
├── Alerts Received:        {total_received}
├── Alerts Quality Rejected:{total_rejected}
├── Data Quality Failures:  {dq_failures}
├── Valid Forward Alerts:   {valid_forward}
├── Observation States:
│   ├── PENDING / ACTIVE:   {pending_count}
│   ├── TERMINAL RESOLVED:  {resolved_count}
│   └── CENSORED (Trunc):   {censored_count}
```

---

## 2. Scanner Forward Governance & Evidence Accumulation Matrix

{df_to_markdown(df_gov)}

---

## 3. Live PULLBACK (v5.1.2 ATR) vs Shadow Control (v5.1.1 Fixed 4%)

$$\\Delta\\text{{Net R}} = \\text{{Net R}}_{{v5.1.2}} - \\text{{Net R}}_{{v5.1.1}}$$

| Live Paired Metric | Current Observed Value | Operational Meaning |
| :--- | :---: | :--- |
| **Resolved Paired Trades ($N$)** | **{n_pb_paired} trades** | Real-time 1-to-1 live forward outcomes |
| **Mean Live $\\Delta\\text{{Net R}}$** | **{mean_pb_delta}** | Continuous real-world treatment effect |
| **Median Live $\\Delta\\text{{Net R}}$** | **{median_pb_delta}** | Skew-adjusted median shift |
| **Live % Trades Improved** | **{pct_pb_improved}** | False stop-outs rescued by ATR buffer |
| **Live % Trades Worsened** | **{pct_pb_worsened}** | Downside leakage from wider stop units |

---

## 4. Strict Governance Rules for Future Releases (v5.1.3+)

> [!IMPORTANT]
> **5-Fold Promotion Acceptance Standard**:
> To prevent premature optimization, no strategy changes are permitted on small sample sizes ($N < 100$).
> A candidate scanner fix will only be considered for promotion to **v5.1.3** if ALL 5 gates pass:
> 1. **Sample Size & Failure Provenance**: Accumulate $N \ge 100$ terminal resolved forward trades proving a reproducible structural weakness in failure anatomy analysis.
> 2. **Controlled Single-Variable Experiment**: Isolated treatment variable with Point-in-Time (PIT) invariance proof (zero future bar leakage).
> 3. **Positive Treatment Effect**: Strictly positive Paired $\\Delta\\text{{Net R}}$ 95% Bootstrap Confidence Interval ($> 0$) on an independent, untouched holdout.
> 4. **Preserved Economic Efficiency**: Net Profit Factor does not deteriorate materially ($\\text{{Net PF}} \\ge 1.30$) and win rate preserves positive expectancy.
> 5. **Risk Budget Compliance**: Maximum Peak-to-Trough Drawdown improves or remains strictly within predefined risk bounds ($\\le 8.0R$), with no new data-quality or execution-friction violations.
> 
> *Note on Censored Observations*: Alerts marked as `CENSORED` (truncated or incomplete observation horizon) are strictly quarantined and excluded from all realized Net R, Win Rate, and PF calculations until terminal resolution.
"""

        with open(report_path, "w") as f:
            f.write(report_content)

        return report_content


if __name__ == "__main__":
    engine = LiveForwardMonitoringEngine()
    
    # Initialize / refresh the governance report
    report = engine.generate_governance_report()
    print("=" * 80)
    print("v5.1.2 LIVE FORWARD MONITORING PIPELINE REFRESHED!")
    print(f"Master Governance Report written to: {MONITORING_REPORT_PATH}")
    print("=" * 80)
