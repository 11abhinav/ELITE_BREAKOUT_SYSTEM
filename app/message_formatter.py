import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def format_alert_payload(alert_data: Dict[str, Any]) -> str:
    """
    Formats clean Telegram/WebPush alert payloads with graded earnings risk warnings
    and quality trajectory badges.
    """
    symbol = alert_data.get("symbol", "N/A")
    scanner = alert_data.get("scanner", "N/A")
    score = alert_data.get("total_score", alert_data.get("base_score", 0))
    entry = float(alert_data.get("entry_price", 0.0) or 0.0)
    sl = float(alert_data.get("stop_loss", 0.0) or 0.0)
    t1 = float(alert_data.get("target_1", 0.0) or 0.0)
    
    warning_msg = alert_data.get("warning_msg", "")
    traj_grade = alert_data.get("trajectory_grade", "N/A")
    traj_score = alert_data.get("trajectory_score", 0)

    lines = [
        f"🟢 <b>ELITE BREAKOUT ALERT</b>: <b>#{symbol}</b>",
        f"Scanner: <code>{scanner}</code> | Score: <b>{score}</b>",
        f"Quality Trajectory: <b>Grade {traj_grade}</b> ({traj_score}/20 pts)",
        f"Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | Target 1: ₹{t1:.2f}"
    ]

    if warning_msg:
        lines.append("")
        lines.append(f"<b>{warning_msg}</b>")

    return "\n".join(lines)

def format_alert(alert_dict: Dict[str, Any], scanner: str = "EOD") -> str:
    symbol = alert_dict.get("symbol", "N/A")
    score = alert_dict.get("score", 0)
    category = alert_dict.get("category", "")
    peg = alert_dict.get("peg")
    yoy_rev = alert_dict.get("yoy_rev")
    yoy_profit = alert_dict.get("yoy_profit")
    sl = alert_dict.get("stop_loss", 0.0)
    t1 = alert_dict.get("target_1", 0.0)

    tier = "ELITE" if score >= 95 else "STANDARD"
    peg_str = "DEEP VALUE" if (peg is not None and peg < 1.0) else ""

    lines = [
        f"🟢 {tier} BREAKOUT ALERT: #{symbol}",
        f"Scanner: {scanner} | Score: {score} | Category: {category}",
        f"Tag: {peg_str}",
        f"YoY Rev: {yoy_rev}% | YoY Profit: {yoy_profit}%",
        f"SL: {sl} | Target 1: {t1}"
    ]
    return "\n".join(lines)

