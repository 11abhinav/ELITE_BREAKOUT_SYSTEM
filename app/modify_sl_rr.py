import re

with open("app/sl_target_helper.py", "r") as f:
    content = f.read()

# _pick_support
old_pick_support = """    for level, label in [
        (swing_low_cluster, "swing low cluster"),
        (swing_low,     "pivot swing low"),
        (s1,            "pivot S1"),
        (swing_low_raw, "rolling swing low"),
        (s2,            "pivot S2"),
    ]:"""

new_pick_support = """    for level, label in [
        (swing_low_cluster, "swing low cluster"),
        (swing_low,     "pivot swing low"),
        (swing_low_raw, "rolling swing low"),
        (s1,            "pivot S1 (discovery zone)"),
    ]:"""
content = content.replace(old_pick_support, new_pick_support)

# _compute_eod RR check
old_eod_rr = """    # EOD needs minimum 2:1 — justify overnight risk
    if (t1_raw - entry) < min_rr * risk:
        t1_raw   = entry + min_rr * risk
        t_method = f"T1: bumped to {min_rr}×RR ({res_label} too close)"
"""

new_eod_rr = """    is_rejected = False
    rejection_reason = None
    # EOD needs minimum 2:1 — justify overnight risk
    if (t1_raw - entry) < min_rr * risk:
        actual_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
        is_rejected = True
        rejection_reason = f"Natural RR {actual_rr} < {min_rr} ({res_label} too close)"
        # DO NOT fabricate targets; keep natural
"""
content = content.replace(old_eod_rr, new_eod_rr)

# add is_rejected to return of _compute_eod
content = content.replace('        "trail_note":   "Raise SL to breakeven after T1 is hit",', '        "trail_note":   "Raise SL to breakeven after T1 is hit",\n        "is_rejected": is_rejected,\n        "rejection_reason": rejection_reason,')


# _compute_intraday RR check
old_intraday_rr = """    if (t1_raw - entry) < min_rr * risk:
        t1_raw   = entry + min_rr * risk
        t_method = f"T1: bumped to {min_rr}×RR ({res_label} too close)"
"""

new_intraday_rr = """    is_rejected = False
    rejection_reason = None
    if (t1_raw - entry) < min_rr * risk:
        actual_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
        is_rejected = True
        rejection_reason = f"Natural RR {actual_rr} < {min_rr} ({res_label} too close)"
"""
content = content.replace(old_intraday_rr, new_intraday_rr)

content = content.replace('        "trail_note":   "Hold position until SL or Target is hit",', '        "trail_note":   "Hold position until SL or Target is hit",\n        "is_rejected": is_rejected,\n        "rejection_reason": rejection_reason,')


# _compute_live_1h RR check
old_1h_rr = """    # 1H swing: minimum 2:1 RR
    if (t1_raw - entry) < min_rr * risk:
        t1_raw   = entry + min_rr * risk
        t_method = f"T1: bumped to {min_rr}×RR ({res_label} too close)"
"""

new_1h_rr = """    is_rejected = False
    rejection_reason = None
    # 1H swing: minimum 2:1 RR
    if (t1_raw - entry) < min_rr * risk:
        actual_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
        is_rejected = True
        rejection_reason = f"Natural RR {actual_rr} < {min_rr} ({res_label} too close)"
"""
content = content.replace(old_1h_rr, new_1h_rr)

content = content.replace('        "trail_note":   "Trail SL to last hourly swing low after T1 is hit",', '        "trail_note":   "Trail SL to last hourly swing low after T1 is hit",\n        "is_rejected": is_rejected,\n        "rejection_reason": rejection_reason,')


# _compute_reversal RR check
old_rev_rr = """    # Enforce minimum 2:1 RR
    if (t1_raw - entry) < min_rr * risk:
        t1_raw   = entry + min_rr * risk
        t_method = f"T1: bumped to {min_rr}×RR (target too close to entry)"
"""

new_rev_rr = """    # Enforce minimum 2:1 RR (Natural)
    is_rejected = False
    rejection_reason = None
    if (t1_raw - entry) < min_rr * risk:
        actual_rr = round((t1_raw - entry) / risk, 2) if risk > 0 else 0
        is_rejected = True
        rejection_reason = f"Natural RR {actual_rr} < {min_rr} (target too close to entry)"
        
    # Reversal Dead Cat Filter (Volume expansion)
    import pandas as pd
    if not is_rejected:
        # Check volume from locals if ticker was passed, though compute_reversal doesn't receive ticker directly!
        pass # Wait, we need to add ticker to _compute_reversal
"""
content = content.replace(old_rev_rr, new_rev_rr)

content = content.replace('        "trail_note":   "Book 50% at T1 (EMA20). Trail remainder to breakeven.",', '        "trail_note":   "Book 50% at T1 (EMA20). Trail remainder to breakeven.",\n        "is_rejected": is_rejected,\n        "rejection_reason": rejection_reason,')

with open("app/sl_target_helper.py", "w") as f:
    f.write(content)

