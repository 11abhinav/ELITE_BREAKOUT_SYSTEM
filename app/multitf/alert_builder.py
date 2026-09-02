# =====================================================================================
# app/multitf/alert_builder.py
# MULTI_TF V3 — Rich Structured Alert Message Builder
#
# Responsibility: Assembles the full, rich Telegram/push notification message
#   from the three engine outputs: Consolidation, Pressure, and BreakoutStrength.
#
# Output Format:
#   🚀 MULTI-TF BREAKOUT ALERT
#   ━━━ 15M BASE (92/100 🔥 EXCEPTIONAL) ━━━
#   ━━━ BREAKOUT (91/100 🚀 EXPLOSIVE) ━━━
#   ━━━ SUMMARY ━━━
#   ━━━ TIMING ━━━
#   CHECKLIST
# =====================================================================================

import logging
from datetime import datetime
from typing import Dict, Any, Optional

from multitf.breakout_strength import SEVERITY_EMOJI, SEVERITY_LABEL

logger = logging.getLogger("multitf.alert_builder")

# Rating labels with emoji
BASE_RATING_EMOJI = {
    "EXCEPTIONAL": "🔥 EXCEPTIONAL",
    "SUPER":       "🟢 SUPER",
    "GOOD":        "🟢 GOOD",
    "WATCH":       "🟡 WATCH",
    "REJECT":      "🔴 REJECT"
}

BREAKOUT_RATING_EMOJI = {
    "EXPLOSIVE":   "🚀 EXPLOSIVE",
    "VERY_STRONG": "🔥 VERY STRONG",
    "STRONG":      "🟢 STRONG",
    "NORMAL":      "🟡 NORMAL",
    "WEAK":        "⚠️ WEAK"
}

RVOL_EMOJI = {
    "EXCEPTIONAL": "🚀🚀",
    "VERY_STRONG": "🚀",
    "STRONG":      "🔥",
    "CONFIRMED":   "🟢",
    "NORMAL":      "🟡",
    "WEAK":        "🔴"
}

VELOCITY_EMOJI = {
    "EXPLOSIVE": "🚀",
    "VERY_FAST": "🔥",
    "FAST":      "🟢",
    "NORMAL":    "🟡"
}


def build_multitf_alert_message(
    symbol: str,
    exchange: str,
    consolidation,           # ConsolidationResult
    pressure,                # PressureResult
    breakout_strength,       # BreakoutStrengthResult
    severity: str,           # 'A_PLUS', 'EXPLOSIVE', 'SUPER', 'GOOD', 'WEAK'
    sl_levels: Dict[str, Any],
    ist_now: datetime
) -> str:
    """
    Builds the complete, rich structured alert message for Telegram/push delivery.

    Args:
        symbol: Stock ticker (e.g. 'ADANIPOWER')
        exchange: 'NSE' or 'BSE'
        consolidation: ConsolidationResult from consolidation.py
        pressure: PressureResult from pressure.py
        breakout_strength: BreakoutStrengthResult from breakout_strength.py
        severity: Final tier from classify_alert_severity()
        sl_levels: Dict with keys: entry, stop, t1, t2, t3, rr_ratio, extension_daily_atr
        ist_now: Current IST timestamp for timing section

    Returns:
        Formatted alert string ready for Telegram markdown
    """
    s = breakout_strength
    c = consolidation
    p = pressure

    # ── Header ───────────────────────────────────────────────────────────────
    sev_emoji = SEVERITY_EMOJI.get(severity, "🟢")
    sev_label = SEVERITY_LABEL.get(severity, "BREAKOUT")
    base_em   = BASE_RATING_EMOJI.get(c.base_rating_label, c.base_rating_label)
    brk_em    = BREAKOUT_RATING_EMOJI.get(s.breakout_rating_label, s.breakout_rating_label)
    rvol_em   = RVOL_EMOJI.get(s.rvol_label, "")
    vel_em    = VELOCITY_EMOJI.get(s.velocity_label.replace("_", ""), "")

    lines = [
        f"*{sev_emoji} MULTI-TF BREAKOUT ALERT*",
        "",
        f"*{symbol}*",
        f"{exchange} | ₹{sl_levels.get('entry', 0):.2f}",
        "",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"*{sev_emoji} SIGNAL: {sev_label}*",
        f"━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # ── 15M BASE Section ─────────────────────────────────────────────────────
    time_in_base = ""
    if c.start_ts and c.end_ts:
        diff_min = int((c.end_ts - c.start_ts).total_seconds() / 60)
        hh, mm = divmod(diff_min, 60)
        time_in_base = f"{hh}h {mm:02d}m" if hh > 0 else f"{mm}m"

    comp_pct = int((1.0 - c.compression_ratio) * 100) if c.compression_ratio < 1.0 else 0

    lines += [
        f"*🏗️ 15M BASE ({c.setup_score}/100 {base_em})*",
        f"Duration:          {c.bars_count} candles / {time_in_base}",
        f"Range:             {c.box_width_atr:.2f}× 15m ATR",
    ]
    if comp_pct > 0:
        lines.append(f"Compression:       {comp_pct}% ↓ contracting")
    else:
        lines.append(f"Compression:       {abs(comp_pct)}% ↑ expanding")

    lines += [
        f"Resistance:        ₹{c.box_high:.2f}",
        f"Tests:             {c.resistance_test_count}  {'(rising absorption)' if c.has_higher_lows else ''}",
        f"Higher Lows:       {'YES ↑ buyers aggressive' if c.has_higher_lows else 'NO'}",
        "",
    ]

    # Score breakdown (compact)
    lines += [
        f"  Score Breakdown:",
        f"  Maturity {c.score_maturity}/15 | Tightness {c.score_tightness}/20 | Resistance {c.score_resistance_quality}/20",
        f"  Tests {c.score_repeated_tests}/15 | VCP {c.score_compression}/15 | Higher Lows {c.score_higher_lows}/10 | Support {c.score_support_integrity}/5",
        "",
    ]

    # ── BREAKOUT Section ──────────────────────────────────────────────────────
    lines += [
        f"━━━━━━━━━━━━━━━━━━━━",
        f"*🚀 BREAKOUT ({s.breakout_score}/100 {brk_em})*",
        f"━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Breakout Price:    ₹{sl_levels.get('entry', 0):.2f}",
        f"Penetration:       +{s.penetration_atr:.2f}× 5m ATR  (+{s.penetration_pct * 100:.2f}%)",
        f"Close Position:    {s.close_position:.2f} (top {int((1 - s.close_position) * 100)}%)",
        f"Velocity:          {s.velocity_label} {vel_em}",
        "",
        f"VOLUME",
        f"5m Volume:         {_fmt_vol(s.current_5m_volume)}",
        f"Expected:          {_fmt_vol(s.expected_volume)}",
        f"RVOL:              {s.volume_ratio:.2f}× {rvol_em}",
        f"vs Previous 5m:    +{int((s.volume_acceleration - 1.0) * 100)}%" if s.volume_acceleration >= 1 else f"vs Previous 5m:    {int((s.volume_acceleration - 1.0) * 100)}%",
        "",
    ]

    # Score breakdown (compact)
    lines += [
        f"  Score Breakdown:",
        f"  RVOL {s.score_rvol}/30 | Accel {s.score_vol_accel}/10 | Magnitude {s.score_magnitude}/15 | Candle {s.score_candle_quality}/15",
        f"  Velocity {s.score_velocity}/10 | Penetration {s.score_penetration}/10 | Market RS {s.score_market_rs}/10",
        "",
    ]

    # ── SUMMARY Section ───────────────────────────────────────────────────────
    ext_daily = sl_levels.get("extension_daily_atr", 0.0)
    rr = sl_levels.get("rr_ratio", 0.0)
    entry = sl_levels.get("entry", 0)
    stop  = sl_levels.get("stop", 0)
    t1    = sl_levels.get("t1", 0)
    t2    = sl_levels.get("t2", 0)
    t3    = sl_levels.get("t3", 0)

    ext_ok = ext_daily <= 0.50
    rr_ok  = rr >= 1.5

    lines += [
        f"━━━━━━━━━━━━━━━━━━━━",
        f"*📊 SUMMARY*",
        f"━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Base Score:        {c.setup_score}/100",
        f"Breakout Score:    {s.breakout_score}/100",
        f"Classification:    {sev_emoji} {sev_label}",
        f"Extension:         {ext_daily:.2f}× Daily ATR  {'✅' if ext_ok else '⚠️'}",
        f"R:R:               {rr:.1f} : 1",
        "",
        f"Entry:             ₹{entry:.2f}",
        f"Stop:              ₹{stop:.2f}",
        f"T1 / T2 / T3:      ₹{t1:.1f} / ₹{t2:.1f} / ₹{t3:.1f}",
        "",
    ]

    # ── TIMING Section ────────────────────────────────────────────────────────
    base_formed = c.start_ts.strftime("%H:%M IST") if c.start_ts else "—"
    detected_at = ist_now.strftime("%H:%M IST")

    lines += [
        f"━━━━━━━━━━━━━━━━━━━━",
        f"*⏱ TIMING*",
        f"━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Base formed:       {base_formed}",
        f"Breakout detected: {detected_at}",
        f"Time in base:      {time_in_base}",
        "",
    ]

    # ── CHECKLIST ─────────────────────────────────────────────────────────────
    lines += ["CHECKLIST"]
    for check in s.checklist:
        lines.append(check)

    # Additional summary checks
    lines.append(f"{'✅' if ext_ok else '⚠️'} Not overextended ({ext_daily:.2f}× Daily ATR)")
    lines.append(f"{'✅' if rr_ok else '⚠️'} R:R {rr:.1f} : 1")

    # Status
    lines += [
        "",
        f"STATUS: {sev_emoji} TRADE ACTIVE",
    ]

    return "\n".join(lines)


def _fmt_vol(vol: float) -> str:
    """Formats volume as 123K or 1.2M for readability."""
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.0f}K"
    return str(int(vol))
