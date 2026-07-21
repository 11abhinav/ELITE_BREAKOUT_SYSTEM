import requests
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Set, Dict, List, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

CACHE_FILE = "data/fii_block_deals.json"

@dataclass(frozen=True)
class EntityPattern:
    name: str                 # Canonical label: "GOLDMAN SACHS"
    tokens: Set[str]          # Primary tokens to match
    min_tokens: int = 2       # How many tokens must match
    aliases: Set[str] = field(default_factory=set) # Alternative acronyms/shorthands

KNOWN_FII_PATTERNS: List[EntityPattern] = [
    EntityPattern("MORGAN STANLEY", {"MORGAN", "STANLEY"}, 2),
    EntityPattern("GOLDMAN SACHS", {"GOLDMAN", "SACHS"}, 2),
    EntityPattern("NOMURA", {"NOMURA"}, 1),
    EntityPattern("SOCIETE GENERALE", {"SOCIETE", "GENERALE"}, 2),
    EntityPattern("VANGUARD", {"VANGUARD"}, 1),
    EntityPattern("BLACKROCK", {"BLACKROCK"}, 1),
    EntityPattern("FIDELITY", {"FIDELITY"}, 1),
    EntityPattern("JP MORGAN", {"JP", "MORGAN"}, 2),
    EntityPattern("CITIGROUP", {"CITIGROUP"}, 1),
    EntityPattern("MERRILL LYNCH", {"MERRILL", "LYNCH"}, 2),
    EntityPattern("BNP PARIBAS", {"BNP", "PARIBAS"}, 2),
    EntityPattern("BOFA SECURITIES", {"BOFA", "SECURITIES"}, 2),
    EntityPattern("NORGES BANK", {"NORGES", "BANK"}, 2),
    EntityPattern("ADIA", {"ABU", "DHABI", "INVESTMENT", "AUTHORITY"}, 3, {"ADIA"})
]

KNOWN_DII_SUPER_PATTERNS: List[EntityPattern] = [
    # Marquee Individuals
    EntityPattern("ASHISH KACHOLIA", {"ASHISH", "KACHOLIA"}, 2),
    EntityPattern("MUKUL AGRAWAL", {"MUKUL", "AGRAWAL"}, 2),
    EntityPattern("VIJAY KEDIA", {"VIJAY", "KEDIA"}, 2),
    EntityPattern("DOLLY KHANNA", {"DOLLY", "KHANNA"}, 2),
    EntityPattern("RADHAKISHAN DAMANI", {"RADHAKISHAN", "DAMANI"}, 2),
    EntityPattern("RARE ENTERPRISES", {"RARE", "ENTERPRISES"}, 2),
    
    # Mutual Funds & Domestic Institutions
    EntityPattern("SBI MUTUAL FUND", {"SBI", "MUTUAL", "FUND"}, 2, {"SBI", "MF", "SBIMF"}),
    EntityPattern("HDFC MUTUAL FUND", {"HDFC", "MUTUAL", "FUND"}, 2, {"HDFC", "MF", "HDFCMF"}),
    EntityPattern("ICICI PRUDENTIAL MUTUAL FUND", {"ICICI", "PRUDENTIAL", "MUTUAL", "FUND"}, 3, {"ICICIPRU", "ICICI", "MF"}),
    EntityPattern("NIPPON INDIA MUTUAL FUND", {"NIPPON", "INDIA", "MUTUAL", "FUND"}, 3, {"NIPPON", "MF"}),
    EntityPattern("KOTAK MUTUAL FUND", {"KOTAK", "MUTUAL", "FUND"}, 2, {"KOTAK", "MF"}),
    EntityPattern("AXIS MUTUAL FUND", {"AXIS", "MUTUAL", "FUND"}, 2, {"AXIS", "MF"}),
    EntityPattern("DSP MUTUAL FUND", {"DSP", "MUTUAL", "FUND"}, 2, {"DSP", "MF"}),
    EntityPattern("UTI MUTUAL FUND", {"UTI", "MUTUAL", "FUND"}, 2, {"UTI", "MF"}),
    EntityPattern("TATA MUTUAL FUND", {"TATA", "MUTUAL", "FUND"}, 2, {"TATA", "MF"}),
    EntityPattern("MIRAE ASSET MUTUAL FUND", {"MIRAE", "ASSET", "MUTUAL", "FUND"}, 3, {"MIRAE", "MF"}),
    EntityPattern("ABAKKUS", {"ABAKKUS"}, 1),
    EntityPattern("WHITE OAK", {"WHITE", "OAK"}, 2),
    EntityPattern("MALABAR", {"MALABAR"}, 1)
]

CORE_TOKENS: Dict[str, Set[str]] = {
    "ADANIENT": {"ADANI"},
    "ADANIPOWER": {"ADANI"},
    "ADANIPORTS": {"ADANI"},
    "TATAPOWER": {"TATA"},
    "TATAMOTORS": {"TATA"},
    "TATASTEEL": {"TATA"},
    "TATACOMM": {"TATA"},
    "TATACHEM": {"TATA"},
    "TATAELXSI": {"TATA"},
    "TATACONSUM": {"TATA"},
    "M&M": {"MAHINDRA"},
    "RELIANCE": {"RELIANCE"},
    "MARUTI": {"MARUTI", "SUZUKI"},
    "BIRLACORPN": {"BIRLA"},
    "JINDALSTEL": {"JINDAL"}
}

def normalize_client_name(raw: str) -> str:
    """Normalizes raw client names to standard uppercase, strips punctuation and suffixes."""
    if not raw or not isinstance(raw, str):
        return ""
    s = raw.upper()
    s = re.sub(r"[^\w\s]", " ", s)  # Drop punctuation
    s = re.sub(r"\s+", " ", s).strip()
    
    # Pad with spaces to prevent edge position misses during replacements
    s = f" {s} "
    s = s.replace(" MF ", " MUTUAL FUND ")
    s = s.replace(" AMC ", " ASSET MANAGEMENT COMPANY ")
    s = re.sub(r"\s+", " ", s).strip()
    
    # Drop corporate suffixes
    for suffix in (" PRIVATE LIMITED", " PVT LTD", " LIMITED", " LTD", " PLC"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()

def name_tokens(raw: str) -> Set[str]:
    """Returns a set of normalized name tokens."""
    return set(normalize_client_name(raw).split())

def match_patterns(tokens: Set[str], raw: str, patterns: List[EntityPattern]) -> List[str]:
    """Matches a client's normalized tokens and raw string against standard EntityPattern definitions."""
    norm = normalize_client_name(raw)
    matches: List[str] = []
    for p in patterns:
        # Token-set match
        if len(tokens & p.tokens) >= p.min_tokens:
            matches.append(p.name)
            continue
        # Alias match (checks normalized string substring or token matching)
        if any(alias in norm for alias in p.aliases) or (tokens & p.aliases):
            matches.append(p.name)
    return matches

def is_promoter_client(symbol: str, client_name: str) -> bool:
    """Returns True if the client name indicates promoter or promoter group buying."""
    tokens = name_tokens(client_name)
    if "PROMOTER" in tokens or "PROMOTERS" in tokens:
        return True
        
    sym = symbol.upper()
    group_tokens = CORE_TOKENS.get(sym, set())
    if group_tokens and (tokens & group_tokens):
        return True
        
    # Ticker prefix fallback matcher (e.g. RELIANCE in RELIANCE INDUSTRIES)
    core_sym = sym.split(".")[0]
    if len(core_sym) >= 4 and core_sym in tokens:
        return True
        
    return False

# Lazy-loaded cache
_CACHE = {}
_LAST_LOADED_DATE = None

def load_cache_if_needed():
    global _CACHE, _LAST_LOADED_DATE
    import os
    today = str(datetime.now(IST).date())
    if _LAST_LOADED_DATE != today or not _CACHE:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        # Normalize symbol keys to uppercase
                        raw_deals = data.get("deals", {})
                        normalized_deals = {}
                        for k, v in raw_deals.items():
                            normalized_deals[k.upper()] = {
                                "fii": list(v.get("fii", [])),
                                "dii_super": list(v.get("dii_super", [])),
                                "promoter": list(v.get("promoter", [])),
                                "fii_sell": list(v.get("fii_sell", [])),
                                "dii_super_sell": list(v.get("dii_super_sell", [])),
                                "promoter_sell": list(v.get("promoter_sell", []))
                            }
                        _CACHE = {
                            "date": today,
                            "version": data.get("version", 1),
                            "deals": normalized_deals
                        }
                        _LAST_LOADED_DATE = today
                        return
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse institutional block deals JSON cache: {e}")
        # Default empty cache
        _CACHE = {"date": today, "version": 1, "deals": {}}
        _LAST_LOADED_DATE = today

def get_inst_footprints(symbol: str) -> dict[str, list[str]]:
    """Side-effect free and shape-stable function returning FII, DII, and Promoter footprints."""
    load_cache_if_needed()
    sym = symbol.strip().upper()
    deals = _CACHE.get("deals", {}).get(sym, {})
    return {
        "fii": list(deals.get("fii", [])),
        "dii_super": list(deals.get("dii_super", [])),
        "promoter": list(deals.get("promoter", [])),
        "fii_sell": list(deals.get("fii_sell", [])),
        "dii_super_sell": list(deals.get("dii_super_sell", [])),
        "promoter_sell": list(deals.get("promoter_sell", []))
    }

def compute_inst_bonus(symbol: str, base_score: Optional[int] = None) -> int:
    """Unified scoring helper to add FII, DII, and Promoter bonuses/penalties with score ceiling enforcements."""
    footprints = get_inst_footprints(symbol)
    bonus = 0
    if footprints["fii"]:
        bonus += 6
    if footprints["dii_super"]:
        bonus += 4
    if footprints["promoter"]:
        bonus += 8

    if footprints["fii_sell"]:
        bonus -= 4
    if footprints["dii_super_sell"]:
        bonus -= 2
    if footprints["promoter_sell"]:
        bonus -= 8

    if base_score is None:
        return bonus

    base = max(0, min(100, int(base_score)))
    # We want to cap the upward bonus, but allow negative penalties to drop score fully
    if bonus > 0:
        return min(100 - base, bonus)
    return bonus

def get_fii_buyers(symbol: str) -> list:
    """Thin backward compatibility wrapper."""
    return get_inst_footprints(symbol).get("fii", [])

def get_nse_bulk_block_deals() -> list:
    """Fetches block/bulk deals from NSE via the nsearchives mirror."""
    import pandas as pd
    import io
    
    urls = [
        "https://nsearchives.nseindia.com/content/equities/bulk.csv",
        "https://nsearchives.nseindia.com/content/equities/block.csv"
    ]
    
    all_deals = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                df = pd.read_csv(io.StringIO(resp.text))
                for _, row in df.iterrows():
                    all_deals.append({
                        'clientName': row.get('Client Name', ''),
                        'symbol': row.get('Symbol', ''),
                        'buyOrSell': row.get('Buy/Sell', ''),
                        'remarks': row.get('Remarks', '')
                    })
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            
    return all_deals

def detect_all_deals() -> dict:
    """Fetches NSE deals and parses FII, DII, and Promoter matches."""
    deals = get_nse_bulk_block_deals()
    if not deals:
        return {}
        
    results = {}
    for deal in deals:
        client = str(deal.get("clientName", "")).upper()
        symbol = str(deal.get("symbol", "")).upper()
        buy_sell = str(deal.get("buyOrSell", deal.get("remarks", ""))).upper()
        
        is_buy = ("BUY" in buy_sell or buy_sell == "B")
        is_sell = ("SELL" in buy_sell or buy_sell == "S")
        
        if not is_buy and not is_sell:
            continue
            
        sym = symbol.strip().upper()
        tokens = name_tokens(client)
        
        fii_matches = match_patterns(tokens, client, KNOWN_FII_PATTERNS)
        dii_super_matches = match_patterns(tokens, client, KNOWN_DII_SUPER_PATTERNS)
        is_prom = is_promoter_client(sym, client)
        
        if fii_matches or dii_super_matches or is_prom:
            if sym not in results:
                results[sym] = {
                    "fii": [], "dii_super": [], "promoter": [],
                    "fii_sell": [], "dii_super_sell": [], "promoter_sell": []
                }
            
            for match in fii_matches:
                target_list = results[sym]["fii"] if is_buy else results[sym]["fii_sell"]
                if match not in target_list:
                    target_list.append(match)
            for match in dii_super_matches:
                target_list = results[sym]["dii_super"] if is_buy else results[sym]["dii_super_sell"]
                if match not in target_list:
                    target_list.append(match)
            if is_prom:
                target_list = results[sym]["promoter"] if is_buy else results[sym]["promoter_sell"]
                if client not in target_list:
                    target_list.append(client)
                    
    return results

def detect_fii_deals() -> dict:
    """Legacy backward compatibility method returning FII deals only."""
    all_deals = detect_all_deals()
    return {sym: val.get("fii", []) for sym, val in all_deals.items() if val.get("fii")}

def get_cached_fii_deals() -> dict:
    """Legacy backward compatibility method."""
    load_cache_if_needed()
    return {sym: data.get("fii", []) for sym, data in _CACHE.get("deals", {}).items() if data.get("fii")}

def run_fii_detector() -> dict:
    """Main execution entrypoint from daily watchlist builder."""
    logger.info("🔍 Running FII Block/Bulk Deal Detector...")
    load_cache_if_needed()
    if _CACHE and _CACHE.get("deals"):
        logger.info(f"✅ Loaded {len(_CACHE.get('deals', {}))} deals from cache.")
        return _CACHE.get("deals", {})

    results = detect_all_deals()
    
    # Save cache
    import os
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "date": str(datetime.now(IST).date()),
                "version": 1,
                "deals": results
            }, f, indent=2)
        logger.info(f"✅ FII/DII/Promoter deals detected in {len(results)} stocks today and cached.")
    except Exception as e:
        logger.error(f"Failed to write cache file {CACHE_FILE}: {e}")
        
    global _LAST_LOADED_DATE
    _CACHE["deals"] = results
    _CACHE["date"] = str(datetime.now(IST).date())
    _CACHE["version"] = 1
    _LAST_LOADED_DATE = str(datetime.now(IST).date())
    
    return results

def get_fii_buyers(symbol: str) -> list:
    """Backward compatibility resolver."""
    return get_inst_footprints(symbol).get("fii", [])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_fii_detector())
