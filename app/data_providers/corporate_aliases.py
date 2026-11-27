"""Centralized corporate action & exchange symbol normalization registry."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

CORPORATE_ACTION_ALIASES: Dict[str, str] = {
    "HEG": "HEGAM",
    "TATAMOTORS": "TMPV",
    "M-M": "M&M",
    "M_M": "M&M",
    "M-MFIN": "M&MFIN",
    "M_MFIN": "M&MFIN",
    "J-KBANK": "J&KBANK",
    "J_KBANK": "J&KBANK",
    "L-TFH": "LTF",
    "L_TFH": "LTF",
    "L&TFH": "LTF",
    "LTFH": "LTF",
    "GVT-D": "GVT&D",
    "GVT_D": "GVT&D",
    "T-IPOWER": "T&IPOWER",
    "T_IPOWER": "T&IPOWER",
    "GUJGAS": "GUJENERGY",
    "GUJGASLTD": "GUJENERGY",
    "GUJRATGAS": "GUJENERGY",
    "GMRINFRA": "GMRAIRPORT",
    "MCDOWELL-N": "UNITDSPR",
    "MCDOWELL": "UNITDSPR",
    "INFIBEAM": "CCAVENUE",
    "ICICIPRULI": "ICICILIFE",
    "LTIM": "LTM",
    "MINDTREE": "LTM",
    "NOVARTIND": "EVONILE",
}

REVERSE_CORPORATE_ALIASES: Dict[str, List[str]] = defaultdict(list)
for _old_sym, _new_sym in CORPORATE_ACTION_ALIASES.items():
    REVERSE_CORPORATE_ALIASES[_new_sym].append(_old_sym)


def strip_exchange_suffixes(symbol: str) -> str:
    """Returns uppercase base symbol with broker prefixes and exchange suffixes stripped."""
    if not symbol:
        return ""
    clean = str(symbol).strip().upper()
    for pfx in ("NSE_EQ|", "NSE_EQ:", "BSE_EQ|", "BSE_EQ:", "NSE:", "BSE:", "MCX:"):
        if clean.startswith(pfx):
            clean = clean[len(pfx):]
            break
    for sfx in (".NS", ".BO", ".BSE"):
        if clean.endswith(sfx):
            clean = clean[:-len(sfx)]
            break
    return clean.strip()


def resolve_corporate_alias(symbol: str) -> str:
    """Resolves any historical corporate-action or ampersand alias to its active symbol."""
    clean = strip_exchange_suffixes(symbol)
    return CORPORATE_ACTION_ALIASES.get(clean, clean)


def get_alias_variants(symbol: str, include_reverse: bool = True) -> List[str]:
    """Returns ordered deduplicated ticker candidates for symbol normalization & lookups."""
    clean = strip_exchange_suffixes(symbol)
    if not clean:
        return []

    candidates: List[str] = [clean]
    if clean in CORPORATE_ACTION_ALIASES:
        candidates.append(CORPORATE_ACTION_ALIASES[clean])

    # Universal exchange character normalization (underscore -> ampersand / hyphen)
    if "_" in clean:
        candidates.append(clean.replace("_", "&"))
        candidates.append(clean.replace("_", "-"))
    if "-" in clean:
        candidates.append(clean.replace("-", "&"))

    if include_reverse:
        for rev in REVERSE_CORPORATE_ALIASES.get(clean, ()):
            candidates.append(rev)

    return list(dict.fromkeys(c for c in candidates if c))
