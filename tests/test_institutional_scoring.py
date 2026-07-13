import os
import json
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from app.block_deal_detector import (
    normalize_client_name,
    name_tokens,
    is_promoter_client,
    get_inst_footprints,
    compute_inst_bonus,
    match_patterns,
    KNOWN_FII_PATTERNS,
    KNOWN_DII_SUPER_PATTERNS,
    load_cache_if_needed,
    _CACHE,
    CACHE_FILE
)

IST = ZoneInfo("Asia/Kolkata")

def test_normalization_and_tokenizer():
    # Suffixes
    assert normalize_client_name("RELIANCE INDUSTRIES LIMITED") == "RELIANCE INDUSTRIES"
    assert normalize_client_name("ADANI GROUP PVT LTD") == "ADANI GROUP"
    assert normalize_client_name("TATA MOTOR LTD") == "TATA MOTOR"
    
    # Abbrev expansions
    assert normalize_client_name("HDFC MF") == "HDFC MUTUAL FUND"
    assert normalize_client_name("SBI AMC") == "SBI ASSET MANAGEMENT COMPANY"
    
    # Punctuation and spacing
    assert normalize_client_name("MORGAN STANLEY & CO. LLC") == "MORGAN STANLEY CO LLC"
    assert name_tokens("HDFC MF") == {"HDFC", "MUTUAL", "FUND"}

def test_pattern_matching_fii_and_dii():
    # FII token-set match
    tokens1 = name_tokens("GOLDMAN SACHS INTL")
    matches1 = match_patterns(tokens1, "GOLDMAN SACHS INTL", KNOWN_FII_PATTERNS)
    assert "GOLDMAN SACHS" in matches1
    
    # FII alias match
    tokens2 = name_tokens("ADIA")
    matches2 = match_patterns(tokens2, "ADIA", KNOWN_FII_PATTERNS)
    assert "ADIA" in matches2
    
    # DII alias match
    tokens3 = name_tokens("HDFCMF")
    matches3 = match_patterns(tokens3, "HDFCMF", KNOWN_DII_SUPER_PATTERNS)
    assert "HDFC MUTUAL FUND" in matches3
    
    tokens4 = name_tokens("SBI MF")
    matches4 = match_patterns(tokens4, "SBI MF", KNOWN_DII_SUPER_PATTERNS)
    assert "SBI MUTUAL FUND" in matches4
    
    # Marquee Super-Investor
    tokens5 = name_tokens("ASHISH KACHOLIA")
    matches5 = match_patterns(tokens5, "ASHISH KACHOLIA", KNOWN_DII_SUPER_PATTERNS)
    assert "ASHISH KACHOLIA" in matches5

def test_promoter_detection():
    # Keyword PROMOTER
    assert is_promoter_client("INFY", "PROMOTER GROUP BUYING") is True
    
    # Core group token
    assert is_promoter_client("ADANIENT", "ADANI FAMILY TRUST") is True
    
    # Ticker prefix fallback
    assert is_promoter_client("RELIANCE", "RELIANCE INDUSTRIAL HOLDINGS") is True
    
    # Negative cases
    assert is_promoter_client("TATASTEEL", "TATA Employees Welfare Trust") is True # Contains TATA core group token
    assert is_promoter_client("INFY", "RANDOM CLIENT NAME") is False

def test_bonus_modifiers_and_caps(monkeypatch):
    # Set up mock cache
    mock_deals = {
        "RELIANCE": {
            "fii": ["NOMURA"],
            "dii_super": ["HDFC MUTUAL FUND"],
            "promoter": ["RELIANCE INDUSTRIES"]
        },
        "ADANIENT": {
            "fii": [],
            "dii_super": [],
            "promoter": ["ADANI FAMILY TRUST"]
        },
        "TCS": {
            "fii": ["MORGAN STANLEY"],
            "dii_super": ["SBI MUTUAL FUND"],
            "promoter": []
        }
    }
    
    # Inject into memory cache
    monkeypatch.setattr("app.block_deal_detector._CACHE", {
        "date": str(datetime.now(IST).date()),
        "version": 1,
        "deals": mock_deals
    })
    monkeypatch.setattr("app.block_deal_detector._LAST_LOADED_DATE", str(datetime.now(IST).date()))
    
    # Test Footprints Getters
    footprints = get_inst_footprints("RELIANCE")
    assert footprints["fii"] == ["NOMURA"]
    assert footprints["dii_super"] == ["HDFC MUTUAL FUND"]
    assert footprints["promoter"] == ["RELIANCE INDUSTRIES"]
    
    # Test compute_inst_bonus - Raw Mode (base_score = None)
    # RELIANCE has FII (+8), DII (+6), Promoter (+6) = 20 pts
    assert compute_inst_bonus("RELIANCE") == 20
    # ADANIENT has Promoter only (+6) = 6 pts
    assert compute_inst_bonus("ADANIENT") == 6
    # TCS has FII (+8), DII (+6) = 14 pts
    assert compute_inst_bonus("TCS") == 14
    
    # Test compute_inst_bonus - Capped Mode
    # RELIANCE with base_score = 90. Capped at 100 - 90 = 10 pts
    assert compute_inst_bonus("RELIANCE", base_score=90) == 10
    # RELIANCE with base_score = 100. Capped at 100 - 100 = 0 pts
    assert compute_inst_bonus("RELIANCE", base_score=100) == 0
    # RELIANCE with base_score = 95. Capped at 100 - 95 = 5 pts
    assert compute_inst_bonus("RELIANCE", base_score=95) == 5
    # RELIANCE with base_score = 40. Full 20 pts (40 + 20 = 60 <= 100)
    assert compute_inst_bonus("RELIANCE", base_score=40) == 20
    # TCS with base_score = 90. TCS has 14 pts raw. Capped to 10
    assert compute_inst_bonus("TCS", base_score=90) == 10
    # TCS with base_score = 80. TCS has 14 pts raw. Full 14 pts (80 + 14 = 94)
    assert compute_inst_bonus("TCS", base_score=80) == 14
    
    # Test edge case base score boundaries (defensive clamping)
    assert compute_inst_bonus("RELIANCE", base_score=-5) == 20 # Base clamped to 0
    assert compute_inst_bonus("RELIANCE", base_score=150) == 0 # Base clamped to 100
