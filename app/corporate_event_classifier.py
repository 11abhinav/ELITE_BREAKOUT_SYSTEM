# =====================================================================================
# app/corporate_event_classifier.py
# DETERMINISTIC CORPORATE EVENT CLASSIFIER, MATERIALITY & HARD-RISK SAFETY GATE
# =====================================================================================

import re
import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST_ZONE = ZoneInfo("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────────────
# TAXONOMY PATTERNS & DETERMINISTIC KEYWORDS
# ─────────────────────────────────────────────────────────────────────────────────────

# 1. Severe Deterministic Hard-Risk Triggers (Bypasses LLM unconditionally)
HARD_RISK_PATTERNS = {
    "CREDIT_DOWNGRADE": [
        r"\b(?:rating|crisil|icra|care|india ratings)\s+(?:downgrade|downgrades|downgraded)\b",
        r"\bdowngrade(?:d)?\s+long\s+term\b",
        r"\brating downgrade\b", r"\bdowngraded to\b",
        r"\bcrisil downgrade\b", r"\bicra downgrade\b", r"\bcare downgrade\b",
        r"\bindia ratings downgrade\b", r"\bdefault rating\b", r"\brating watch with negative\b"
    ],
    "DEBT_DEFAULT": [
        r"\bpayment default\b", r"\bdebt default\b", r"\bdefaulted on\b",
        r"\bdefault in (?:payment|debt|servicing)\b",
        r"\bdelay in (?:payment|debt servicing|interest)\b",
        r"\bnclt\b", r"\binsolvency\b", r"\bibc\b", r"\bcorporate insolvency\b",
        r"\bdebt restructuring\b", r"\bstrategic debt restructuring\b"
    ],
    "AUDITOR_RESIGNATION": [
        r"\bresignation of (?:statutory|joint)?\s*auditor\b",
        r"\bauditor (?:has resigned|resignation)\b",
        r"\badverse (?:opinion|audit)\b", r"\bdisclaimer of opinion\b",
        r"\bforensic audit\b", r"\bqualified opinion\b", r"\bmaterial weakness\b"
    ],
    "REGULATORY_PENALTY": [
        r"\bsebi (?:order|penalty|investigation|search|raid)\b",
        r"\brbi (?:penalty|restriction|ban)\b",
        r"\bed (?:search|raid|investigation|attachment)\b",
        r"\bincome tax (?:search|raid|survey)\b",
        r"\btrading suspension\b", r"\bshow cause notice\b"
    ]
}

# 2. Bullish Growth Catalysts
CATALYST_PATTERNS = {
    "ORDER_WIN": [
        r"\bbagged order\b", r"\breceipt of order\b", r"\bawarded contract\b",
        r"\bwon contract\b", r"\bwork order\b", r"\bletter of intent\b",
        r"\bloi received\b", r"\bmajor contract\b", r"\border book\b"
    ],
    "CAPEX_EXPANSION": [
        r"\bcommercial production\b", r"\bcapacity expansion\b",
        r"\bcommissioned plant\b", r"\bcommissioning of\b", r"\bnew facility\b",
        r"\bexpansion project\b", r"\bgreenfield\b", r"\bbrownfield\b"
    ],
    "M_AND_A": [
        r"\bacquisition of\b", r"\bstrategic acquisition\b", r"\bjoint venture\b",
        r"\bmerger\b", r"\bamalgamation\b", r"\bshare purchase agreement\b"
    ],
    "CAPITAL_ACTION": [
        r"\bbuyback\b", r"\bshare buyback\b", r"\bbonus issue\b",
        r"\bstock split\b", r"\brights issue\b", r"\bdividend declaration\b"
    ],
    "CONCALL_TRANSCRIPT": [
        r"\btranscript\b", r"\bearnings call transcript\b",
        r"\binvestor presentation\b", r"\bearnings presentation\b",
        r"\bcon\.? call\b", r"\binvestor meet\b"
    ]
}


def compute_document_hash(source_url: str, headline: str, publication_date: str) -> str:
    """Computes a deterministic SHA-256 hash for document deduplication."""
    payload = f"{source_url.strip()}|{headline.strip().lower()}|{publication_date.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_inr_amount_cr(text: str) -> Optional[float]:
    """Extracts numeric INR amount in Crores from announcement text if present."""
    if not text:
        return None
    # Matches patterns like: Rs. 4,200 Cr, INR 250 Crores, 1,200.50 cr
    match = re.search(r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)\s*(?:cr(?:ore)?s?|crore)", text, re.IGNORECASE)
    if match:
        raw_val = match.group(1).replace(",", "").strip()
        try:
            return float(raw_val)
        except ValueError:
            pass
    return None


def classify_announcement_text(
    headline: str,
    description: str = "",
    source_name: str = "NSE"
) -> Dict[str, Any]:
    """
    Deterministic rule-based classifier that categorizes raw corporate filings,
    assigns epistemic evidence_class, and extracts materiality parameters.
    """
    combined_text = f"{headline} {description}".lower()
    
    # 1. Epistemic Evidence Class & Source Tier
    source_clean = source_name.upper().strip()
    if source_clean in ("NSE", "BSE", "SEBI", "RBI", "NCLT", "COURT"):
        source_tier = "SOURCE_TIER_1"
        evidence_class = "FACT"
    elif any(agency in source_clean for agency in ("CRISIL", "ICRA", "CARE", "INDIA RATINGS", "AUDITOR")):
        source_tier = "SOURCE_TIER_2"
        evidence_class = "VERIFIED_ASSESSMENT"
    elif any(broker in source_clean for broker in ("BROKER", "ANALYST", "INVESTMENT BANK", "RESEARCH")):
        source_tier = "SOURCE_TIER_2"
        evidence_class = "ANALYST_OPINION"
    else:
        source_tier = "SOURCE_TIER_3"
        evidence_class = "FACT"

    # 2. Check Severe Hard-Risk Triggers
    detected_category = "GENERAL_ANNOUNCEMENT"
    severity_score = 0.0
    hard_risk_flag = False
    
    for cat, patterns in HARD_RISK_PATTERNS.items():
        if any(re.search(pat, combined_text) for pat in patterns):
            detected_category = cat
            hard_risk_flag = True
            if cat == "DEBT_DEFAULT":
                severity_score = 10.0
            elif cat == "AUDITOR_RESIGNATION":
                severity_score = 9.5
            elif cat == "REGULATORY_PENALTY":
                severity_score = 8.5
            elif cat == "CREDIT_DOWNGRADE":
                severity_score = 7.5
            break

    # 3. Check Bullish Growth Catalysts if no hard risk
    if not hard_risk_flag:
        for cat, patterns in CATALYST_PATTERNS.items():
            if any(re.search(pat, combined_text) for pat in patterns):
                detected_category = cat
                break

    # 4. Extract Quantitative Materiality & Order Quality
    order_val_cr = extract_inr_amount_cr(combined_text) if detected_category == "ORDER_WIN" else None
    
    order_quality = None
    materiality_score = 5.0
    
    if detected_category == "ORDER_WIN":
        materiality_score = 6.0
        if order_val_cr:
            if order_val_cr >= 1000.0:
                materiality_score = 9.0
            elif order_val_cr >= 250.0:
                materiality_score = 7.5
            elif order_val_cr >= 50.0:
                materiality_score = 6.5
            else:
                materiality_score = 5.0
        # Quality assessment
        if any(kw in combined_text for kw in ("multi-year", "defense", "export", "railway", "ongc", "isro", "high margin")):
            order_quality = "HIGH"
            materiality_score = min(10.0, materiality_score + 1.0)
        elif any(kw in combined_text for kw in ("single customer", "sub-contract", "low margin")):
            order_quality = "LOW"
            materiality_score = max(3.0, materiality_score - 1.5)
        else:
            order_quality = "MEDIUM"

    elif detected_category == "CAPEX_EXPANSION":
        materiality_score = 7.5
    elif detected_category == "M_AND_A":
        materiality_score = 7.0
    elif detected_category == "CAPITAL_ACTION":
        materiality_score = 6.0 if "buyback" in combined_text else 5.0
    elif hard_risk_flag:
        materiality_score = max(7.0, severity_score)

    return {
        "category": detected_category,
        "evidence_class": evidence_class,
        "source_tier": source_tier,
        "is_hard_risk": hard_risk_flag,
        "severity_score": severity_score,
        "materiality_score": materiality_score,
        "order_value_inr_cr": order_val_cr,
        "order_quality": order_quality,
        "decay_half_life_days": 180 if hard_risk_flag else (90 if detected_category == "ORDER_WIN" else 60)
    }


def evaluate_deterministic_hard_risk_gate(events: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """
    [CONTROL 4: 100% DETERMINISTIC SAFETY GATE PRECEDENCE]
    Evaluates active OPEN corporate events and returns ('QUARANTINE' | 'PASS')
    unconditionally without depending on LLM execution.
    """
    active_hazards = []
    
    for ev in events:
        status = str(ev.get("status", "OPEN")).upper()
        if status != "OPEN":
            continue
            
        category = str(ev.get("category", "")).upper()
        tier = str(ev.get("source_tier", "SOURCE_TIER_1")).upper()
        
        # Hard risk triggered only on Tier-1 and Tier-2 verified sources
        if tier in ("SOURCE_TIER_1", "SOURCE_TIER_2"):
            if category in ("DEBT_DEFAULT", "AUDITOR_RESIGNATION", "REGULATORY_PENALTY", "CREDIT_DOWNGRADE"):
                active_hazards.append(ev)
                
    if active_hazards:
        logger.warning(f"🚨 [DETERMINISTIC SAFETY GATE] Quarantined with {len(active_hazards)} active severe hazard(s)!")
        return "QUARANTINE", active_hazards
        
    return "PASS", []
