"""
Canonical Industry Normalizer
Maps raw data provider industry strings to canonical keys used in engine_config.yaml.
"""
import re

INDUSTRY_MAP = {
    # IT & Software
    "it services": "IT_SERVICES",
    "it consulting": "IT_SERVICES",
    "software services": "IT_SERVICES",
    "software-infrastructure": "IT_PRODUCTS",
    "software infrastructure": "IT_PRODUCTS",
    "software": "IT_PRODUCTS",
    
    # Financials
    "banks - private": "PRIVATE_BANK",
    "private banks": "PRIVATE_BANK",
    "private bank": "PRIVATE_BANK",
    "banks - public": "PSU_BANK",
    "public banks": "PSU_BANK",
    "psu bank": "PSU_BANK",
    
    # Power & Utilities
    "power generation": "POWER_GENERATION",
    "utilities - independent power producers": "POWER_GENERATION",
    "power transmission": "POWER_TRANSMISSION",
    "power & transmission": "POWER_TRANSMISSION",
}

def normalize_industry(raw_industry: str) -> str:
    """
    Normalizes a raw industry string into a canonical industry key.
    Falls back to 'DEFAULT' if no mapping matches.
    """
    if not raw_industry:
        return "DEFAULT"
        
    cleaned = raw_industry.lower().strip()
    
    # Exact match first
    if cleaned in INDUSTRY_MAP:
        return INDUSTRY_MAP[cleaned]
        
    # Substring matching for fuzzy resolution
    for raw_key, canonical in INDUSTRY_MAP.items():
        if raw_key in cleaned or cleaned in raw_key:
            return canonical
            
    return "DEFAULT"
