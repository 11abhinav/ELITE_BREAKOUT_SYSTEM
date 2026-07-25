# =====================================================================================
# app/stock_analyzer.py
# ON-DEMAND STOCK ANALYZER, FUNNEL DIAGNOSTICS & MANUAL ALERT ENGINE
# =====================================================================================

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from indicator_manager import manager
import swing_utils
import scoring_engine
from macro_utils import compute_nifty_rs_rating, MarketRegimeEngine
from price_cache import fetch_watchlist_data
from fundamentals_cache import get_fundamentals
from watchlist_cache import get_watchlist
from sl_target_helper import compute_sl_and_target
from database import (
    init_db, get_connection, save_alert_if_new,
    get_user_watchlist, update_user_watchlist_scan_result
)
from config import EOD_CONFIG, REVERSAL_CONFIG, PULLBACK_CONFIG, MULTI_TF_CONFIG

logger = logging.getLogger("stock_analyzer")
IST = ZoneInfo("Asia/Kolkata")


def validate_nse_bse_ticker(symbol: str) -> dict:
    """
    Validates if the provided ticker symbol is a recognized NSE/BSE Indian stock ticker.
    Checks watchlist cache, database symbol_mappings, and market price data fetcher.
    """
    if not symbol or not isinstance(symbol, str) or len(symbol.strip()) < 1:
        return {
            "is_valid": False,
            "error": "Symbol input cannot be empty. Please enter a valid NSE/BSE stock ticker (e.g. TATAMOTORS, RELIANCE, PERSISTENT)."
        }

    raw = symbol.strip().upper()
    sym_clean = raw.replace('.NS', '').replace('.BO', '')

    import re
    if not re.match(r"^[A-Z0-9&\-]{2,20}$", sym_clean):
        return {
            "is_valid": False,
            "error": f"Invalid ticker format '{symbol}'. NSE/BSE stock symbols contain only letters, numbers, hyphens, and ampersands (e.g. TATAMOTORS, M&M, BAJAJ-AUTO)."
        }

    company_name = sym_clean
    sector_name = "EQUITY"
    found = False

    # 1. Check Watchlist Cache (~2000+ active NSE/BSE symbols)
    try:
        wl = get_watchlist()
        if not wl.empty:
            match = wl[wl['Stock'].str.upper() == sym_clean]
            if not match.empty:
                found = True
                row = match.iloc[0]
                company_name = str(row.get("Company", sym_clean))
                sector_name = str(row.get("Sector", "EQUITY"))
    except Exception as e:
        logger.warning(f"Watchlist validation lookup warning for {sym_clean}: {e}")

    # 2. Check Database symbol_mappings
    if not found:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT name, sector
                        FROM symbol_mappings
                        WHERE UPPER(nse_symbol) = %s OR UPPER(bse_symbol) = %s
                        LIMIT 1
                    """, (sym_clean, sym_clean))
                    row = cur.fetchone()
                    if row and isinstance(row, (tuple, list)) and len(row) > 0 and row[0] is not None and not hasattr(row[0], '_mock_name'):
                        found = True
                        company_name = str(row[0]) if row[0] else sym_clean
                        sector_name = str(row[1]) if len(row) > 1 and row[1] else "EQUITY"
        except Exception:
            pass

    # 3. Live Price Cache verification fallback
    if not found:
        try:
            sample_df = pd.DataFrame([{"Stock": sym_clean, "Category": "MIDCAP", "Sector": "GENERAL"}])
            fetched_map = fetch_watchlist_data(sample_df, "1mo", "1d", requester="TICKER_VALIDATOR")
            df = fetched_map.get(sym_clean) or fetched_map.get(f"{sym_clean}.NS") or fetched_map.get(f"{sym_clean}.BO")
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty and len(df) >= 5:
                found = True
        except Exception:
            pass

    if not found:
        return {
            "is_valid": False,
            "error": f"❌ '{sym_clean}' is NOT a recognized NSE/BSE ticker symbol. Please correct the stock ticker (e.g. TATAMOTORS, RELIANCE, PERSISTENT) or choose from the autocomplete dropdown list."
        }

    return {
        "is_valid": True,
        "symbol": sym_clean,
        "company_name": company_name,
        "sector": sector_name
    }


def search_symbols_autocomplete(query: str, limit: int = 10) -> list:
    """
    Real-time autocomplete search returning matching NSE/BSE symbols & company titles.
    Matches ticker prefix first, then company name substring.
    """
    if not query or len(query.strip()) < 1:
        return []

    q_clean = query.strip().upper()
    results = []
    seen = set()

    # 1. Search in fundamental watchlist cache (Vectorized for speed)
    try:
        wl = get_watchlist()
        if not wl.empty:
            stock_series = wl['Stock'].astype(str).str.upper()
            company_series = wl['Company'].astype(str).str.upper()
            
            # Fast boolean mask
            mask = (stock_series.str.startswith(q_clean)) | (stock_series.str.contains(q_clean, regex=False)) | (company_series.str.contains(q_clean, regex=False))
            
            matches = wl[mask].head(limit)
            
            for _, row in matches.iterrows():
                stock = str(row.get("Stock", "")).upper()
                company = str(row.get("Company", stock))
                sector = str(row.get("Sector", ""))
                category = str(row.get("Category", ""))

                if stock not in seen:
                    seen.add(stock)
                    results.append({
                        "symbol": stock,
                        "company_name": company if company != stock else stock,
                        "sector": sector,
                        "category": category
                    })
            
            if len(results) >= limit:
                return results
    except Exception as e:
        logger.warning(f"Autocomplete watchlist lookup warning: {e}")

    return results[:limit]


def analyze_symbol(symbol: str, user_id: str = "DEFAULT_USER") -> dict:
    """
    Runs full dry-run multi-scanner diagnostic evaluation for a single ticker symbol.
    Validates ticker symbol first; returns structured error if invalid NSE/BSE stock ticker.
    """
    # 0. Validate NSE/BSE Ticker
    val = validate_nse_bse_ticker(symbol)
    if not val["is_valid"]:
        return {
            "symbol": symbol.strip().upper() if symbol else "",
            "success": False,
            "is_invalid_ticker": True,
            "error": val["error"]
        }

    sym_clean = val["symbol"]
    ist_now = datetime.now(IST)

    # 1. Fetch OHLCV Market Data
    sample_df = pd.DataFrame([{"Stock": sym_clean, "Category": "MIDCAP", "Sector": "GENERAL"}])
    fetched_map = fetch_watchlist_data(sample_df, "1y", "1d", requester="STOCK_ANALYZER")

    df = fetched_map.get(sym_clean)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        # Fallback fetch retry
        df = fetched_map.get(f"{sym_clean}.NS") or fetched_map.get(f"{sym_clean}.BO")

    if df is None or not isinstance(df, pd.DataFrame) or df.empty or len(df) < 20:
        return {
            "symbol": sym_clean,
            "success": False,
            "error": f"Insufficient or missing historical price data for symbol '{sym_clean}'. Minimum 20 daily bars required."
        }

    df = df.copy()
    df.dropna(subset=["Open", "High", "Low", "Close", "Volume"], inplace=True)
    df.attrs['adjusted'] = True
    df.attrs['symbol'] = sym_clean

    last_bar = df.iloc[-1]
    close_price = float(last_bar['Close'])
    open_price = float(last_bar['Open'])
    high_price = float(last_bar['High'])
    low_price = float(last_bar['Low'])
    volume_val = float(last_bar['Volume'])

    # Compute base indicators
    bundle = manager.compute_base_indicators(df, sym_clean)

    # Fetch Fundamentals from Piotroski cache
    fund_data = get_fundamentals(sym_clean) or {}
    
    # Defaults
    company_name = sym_clean
    sector_name = "GENERAL"
    roce_val = 0.0
    roe_val = 0.0
    debt_equity = 0.0
    
    # Fetch Core fundamental ratios from watchlist cache
    try:
        from watchlist_cache import get_watchlist
        wl = get_watchlist()
        if not wl.empty:
            match = wl[wl['Stock'].str.upper() == sym_clean]
            if not match.empty:
                row = match.iloc[0]
                company_name = str(row.get("Company", company_name))
                sector_name = str(row.get("Sector", sector_name))
                
                raw_roce = row.get("ROCE %")
                if raw_roce is not None and not pd.isna(raw_roce):
                    roce_val = float(raw_roce)
                    
                raw_roe = row.get("ROE %")
                if raw_roe is not None and not pd.isna(raw_roe):
                    roe_val = float(raw_roe)
                    
                raw_de = row.get("Debt/Equity", row.get("Debt to equity"))
                if raw_de is not None and not pd.isna(raw_de):
                    debt_equity = float(raw_de)
    except Exception as e:
        logger.warning(f"Failed to fetch watchlist fundamentals for {sym_clean}: {e}")

    # Compute RS Percentile
    rs_dict = compute_nifty_rs_rating([sym_clean])
    rs_percentile = float(rs_dict.get(sym_clean, 50.0))

    # Deficits collection list
    deficits = []

    # ---------------- STAGE 1: DAILY BUILDER (UNIVERSE ELIGIBILITY) ----------------
    db_pass = True
    db_reasons = []

    if close_price < 100.0:
        db_pass = False
        db_reasons.append(f"Price ₹{close_price:.2f} < ₹100.0 minimum price floor")
        deficits.append(f"💵 Price Floor Deficit: Current price ₹{close_price:.2f} is below the ₹100.0 universe entry threshold.")

    history_len = len(df)
    if history_len < 50:
        db_pass = False
        db_reasons.append(f"Bar history {history_len} < 50 minimum required daily bars")
        deficits.append(f"📅 History Deficit: Symbol has only {history_len} daily bars (requires ≥50 bars).")

    avg_turnover_20d = (df['Close'] * df['Volume']).tail(20).mean() / 1e7 # in Cr
    if avg_turnover_20d < 1.0:
        db_pass = False
        db_reasons.append(f"20D Avg Turnover ₹{avg_turnover_20d:.2f}Cr < ₹1.0Cr minimum liquidity")
        deficits.append(f"💧 Liquidity Deficit: 20-day average turnover ₹{avg_turnover_20d:.2f}Cr is below ₹1.0Cr minimum.")

    if db_pass:
        db_reasons.append(f"Price ₹{close_price:.2f} ≥ ₹100.0 | Avg Turnover ₹{avg_turnover_20d:.1f}Cr ≥ ₹1.0Cr | Bars {history_len} ≥ 50")

    # Default indicators
    vol_ratio = 1.0

    # ---------------- STAGE 2: EOD BREAKOUT SCANNER ----------------
    eod_status = "NO"
    eod_reasons = []

    if not db_pass:
        eod_reasons.append("Skipped (Failed Daily Builder Universe Gate)")
    else:
        # Check Breakout Close
        prior_20d_high = float(df['High'].iloc[-21:-1].max()) if len(df) >= 21 else float(df['High'].max())
        is_breakout = close_price > prior_20d_high

        vol_20d_med = float(df['Volume'].iloc[-21:-1].median()) if len(df) >= 21 else float(df['Volume'].median())
        vol_ratio = (volume_val / vol_20d_med) if vol_20d_med > 0 else 1.0

        candle_range = high_price - low_price
        candle_body = abs(close_price - open_price)
        upper_wick = high_price - max(close_price, open_price)
        body_ratio = (candle_body / candle_range) if candle_range > 0 else 0.0
        wick_ratio = (upper_wick / candle_range) if candle_range > 0 else 0.0

        sma50_val = bundle.sma_50.iloc[-1] if bundle.sma_50 is not None and not bundle.sma_50.empty else None
        sma200_val = bundle.sma_200.iloc[-1] if bundle.sma_200 is not None and not bundle.sma_200.empty else None

        eod_checks = []
        if not is_breakout:
            eod_checks.append(f"Close ₹{close_price:.2f} ≤ Prior 20D High ₹{prior_20d_high:.2f}")
        if vol_ratio < 1.8:
            eod_checks.append(f"Volume Ratio {vol_ratio:.2f}x < 1.8x threshold")
            deficits.append(f"🔊 Volume Surge Deficit: Current Volume Ratio is {vol_ratio:.2f}x (lacks +{max(0.0, 1.8 - vol_ratio):.2f}x for 1.8x EOD threshold).")
        if wick_ratio > 0.35:
            eod_checks.append(f"Upper Wick {wick_ratio*100:.1f}% > 35% max")
            deficits.append(f"🕯️ Upper Wick Deficit: Upper Wick is {wick_ratio*100:.1f}% of candle range (needs ≤35% for clean breakout close).")
        if close_price <= open_price:
            eod_checks.append("Candle is not bullish (Close ≤ Open)")

        if not eod_checks:
            eod_status = "YES"
            eod_reasons.append(f"Clean Breakout Close (₹{close_price:.2f} > ₹{prior_20d_high:.2f}) | Volume Surge {vol_ratio:.2f}x ≥ 1.8x | Bullish Candle")
        else:
            eod_status = "NO"
            eod_reasons = eod_checks

    # ---------------- STAGE 3: MULTI-TF INTRADAY SCANNER ----------------
    mtf_status = "NO"
    mtf_reasons = []
    if not db_pass:
        mtf_reasons.append("Skipped (Failed Daily Builder Universe Gate)")
    else:
        mtf_reasons.append("Intraday 15-minute volume explosion spike required during market hours (09:30–14:45 IST)")

    # ---------------- STAGE 4: REVERSAL OVERSOLD BOUNCE ----------------
    rev_status = "NO"
    rev_reasons = []
    rsi_series = bundle.rsi_14 if hasattr(bundle, 'rsi_14') else None
    rsi_val = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty else 50.0

    if rsi_val <= 35.0:
        rev_status = "YES"
        rev_reasons.append(f"Daily RSI {rsi_val:.1f} ≤ 35.0 Oversold threshold")
    else:
        rev_status = "NO"
        rev_reasons.append(f"Daily RSI {rsi_val:.1f} > 35.0 (Not in oversold bounce zone)")
        if rsi_val > 60:
            deficits.append(f"🔄 Reversal RSI Deficit: RSI is {rsi_val:.1f} (requires RSI ≤ 35.0 for mean-reversion bounce).")

    # ---------------- STAGE 5: PULLBACK CONTINUATION PIPELINE ----------------
    pb_status = "NO"
    pb_reasons = []

    sma50 = bundle.sma_50.iloc[-1] if bundle.sma_50 is not None and not bundle.sma_50.empty else None
    sma200 = bundle.sma_200.iloc[-1] if bundle.sma_200 is not None and not bundle.sma_200.empty else None
    is_uptrend = (sma50 and sma200 and close_price > sma50 > sma200)

    if not is_uptrend:
        pb_status = "NO"
        pb_reasons.append("Trend Failure: Price not strictly above SMA50 > SMA200")
        deficits.append("📈 Trend Structure Deficit: Price is not aligned above SMA50 > SMA200 (requires established uptrend).")
    else:
        pivots = swing_utils.detect_confirmed_pivots(df, 5, 2)
        if pivots:
            pb_status = "YES"
            pb_reasons.append(f"Established Uptrend (Close ₹{close_price:.2f} > SMA50 ₹{sma50:.2f} > SMA200 ₹{sma200:.2f}) | Valid Swing Structure")
        else:
            pb_status = "NO"
            pb_reasons.append("No confirmed swing pivots detected for pullback origin")

    # ---------------- STAGE 6: WEALTH ENGINE ----------------
    we_status = "NO"
    we_reasons = []

    # roce_val, roe_val, and debt_equity are already fetched from watchlist above

    we_issues = []
    if roce_val < 20.0:
        we_issues.append(f"ROCE {roce_val:.1f}% < 20.0% min")
        deficits.append(f"💎 ROCE Quality Deficit: ROCE is {roce_val:.1f}% (requires ≥20.0% for Wealth Engine).")
    if roe_val < 15.0:
        we_issues.append(f"ROE {roe_val:.1f}% < 15.0% min")
        deficits.append(f"💎 ROE Quality Deficit: ROE is {roe_val:.1f}% (requires ≥15.0% for Wealth Engine).")
    if debt_equity > 0.5:
        we_issues.append(f"Debt-to-Equity {debt_equity:.2f} > 0.50 max")
        deficits.append(f"🏦 Leverage Deficit: Debt to Equity is {debt_equity:.2f} (requires ≤0.50 debt free/low debt).")

    if not we_issues:
        we_status = "YES"
        we_reasons.append(f"Fundamentally Pristine: ROCE {roce_val:.1f}% ≥ 20% | ROE {roe_val:.1f}% ≥ 15% | D/E {debt_equity:.2f} ≤ 0.5")
    elif roce_val >= 15.0 and roe_val >= 12.0:
        we_status = "WATCHLIST"
        we_reasons = we_issues
    else:
        we_status = "NO"
        we_reasons = we_issues

    # ---------------- STAGE 7: MULTIBAGGER ENGINE ----------------
    mb_status = "NO"
    mb_reasons = []

    f_score = fund_data.get("piotroski_score", 6)
    pledge_pct = fund_data.get("promoter_pledge_pct", 0.0)

    mb_issues = []
    if f_score < 7:
        mb_issues.append(f"Piotroski F-Score {f_score} < 7 min")
        deficits.append(f"📊 Piotroski F-Score Deficit: F-Score is {f_score}/9 (requires F-Score ≥7 for Prime Multibagger alert).")
    if pledge_pct > 10.0:
        mb_issues.append(f"Promoter Pledge {pledge_pct:.1f}% > 10.0% max")
        deficits.append(f"🔒 Promoter Pledge Deficit: Promoter Pledge is {pledge_pct:.1f}% (requires ≤10.0%).")

    if not mb_issues and is_uptrend:
        mb_status = "YES (Prime)"
        mb_reasons.append(f"🚀 Prime Compounder: Piotroski {f_score}/9 | Pledge {pledge_pct:.1f}% ≤ 10% | Strong Trend")
    elif f_score >= 5 and pledge_pct <= 15.0:
        mb_status = "WATCHLIST"
        mb_reasons = mb_issues if mb_issues else ["Score in Watchlist tier (50-64)"]
    else:
        mb_status = "NO"
        mb_reasons = mb_issues

    # ---------------- COMPOSITE HEALTH SCORE CALCULATION ----------------
    tech_score = 50.0
    if is_uptrend:
        tech_score += 20.0
    if vol_ratio >= 1.5:
        tech_score += 15.0
    if rsi_val >= 50.0 and rsi_val <= 70.0:
        tech_score += 15.0

    fund_score = 50.0
    if roce_val >= 20.0:
        fund_score += 20.0
    if roe_val >= 15.0:
        fund_score += 15.0
    if debt_equity <= 0.5:
        fund_score += 15.0

    overall_health_score = min(100.0, round((tech_score * 0.5) + (fund_score * 0.3) + (rs_percentile * 0.2), 1))

    # Clean duplicates in deficits list (max 4 deficits)
    deficits = list(dict.fromkeys(deficits))[:4]
    if not deficits:
        deficits.append("🌟 Pristine Setup: No significant technical or fundamental deficits detected! Stock is in prime alignment.")

    # Update User Watchlist database state if exists
    update_user_watchlist_scan_result(sym_clean, user_id, health_score=overall_health_score, status="QUALIFIED" if (eod_status=="YES" or pb_status=="YES" or mb_status=="YES (Prime)") else "MONITORING")

    # Check if symbol is already in user watchlist
    user_watchlist = get_user_watchlist(user_id)
    watchlist_symbols = {item["symbol"] for item in (user_watchlist or [])}
    is_in_watchlist = (sym_clean in watchlist_symbols)

    return {
        "symbol": sym_clean,
        "company_name": company_name,
        "sector": sector_name,
        "success": True,
        "is_in_watchlist": is_in_watchlist,
        "close_price": close_price,
        "volume_ratio": round(vol_ratio, 2),
        "rsi": round(rsi_val, 1),
        "overall_health_score": overall_health_score,
        "technical_score": round(tech_score, 1),
        "fundamental_score": round(fund_score, 1),
        "rs_percentile": round(rs_percentile, 1),
        "deficits": deficits,
        "funnel": {
            "daily_builder": {"status": "YES" if db_pass else "NO", "reasons": db_reasons},
            "eod_breakout": {"status": eod_status, "reasons": eod_reasons},
            "multi_tf": {"status": mtf_status, "reasons": mtf_reasons},
            "reversal": {"status": rev_status, "reasons": rev_reasons},
            "pullback": {"status": pb_status, "reasons": pb_reasons},
            "wealth_engine": {"status": we_status, "reasons": we_reasons},
            "multibagger": {"status": mb_status, "reasons": mb_reasons}
        }
    }


def create_manual_alert_from_analysis(symbol: str, scanner_type: str = "EOD", user_id: str = "DEFAULT_USER") -> dict:
    """
    Promotes a qualified analysis result to an ACTIVE BUY ALERT in the database.
    Calculates exact Entry, Stop Loss, Target 1/2/3, Scores, and dispatches Telegram notification.
    """
    sym_clean = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
    scanner_type = scanner_type.strip().upper()

    res = analyze_symbol(sym_clean, user_id=user_id)
    if not res.get("success"):
        return {"success": False, "error": res.get("error", "Analysis failed")}

    entry_price = float(res.get("close_price", 100.0))
    atr_est = entry_price * 0.025 # 2.5% ATR approximation
    sl_target = compute_sl_and_target(entry_price=entry_price, atr=atr_est, mode=scanner_type)

    if sl_target.get("is_rejected"):
        return {"success": False, "error": f"Risk engine rejected target calculation: {sl_target.get('rejection_reason')}"}

    ist_now = datetime.now(IST)
    score_val = int(res.get("overall_health_score", 85))

    saved, reason, alert_id, _ = save_alert_if_new(
        symbol=sym_clean,
        breakout_type=scanner_type,
        alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
        scanner=scanner_type,
        category=f"{scanner_type} (MANUAL)",
        entry_price=entry_price,
        stop_loss=sl_target.get("stop_loss"),
        target_1=sl_target.get("target_1"),
        target_2=sl_target.get("target_2"),
        target_3=sl_target.get("target_3"),
        score=score_val,
        context={"is_manual": True, "created_by": user_id, "analysis_snapshot": res.get("funnel")},
        base_score=score_val,
        rs_bonus=5,
        sector_bonus=5,
        rs_percentile=res.get("rs_percentile", 80.0),
        sector_name=res.get("sector", "GENERAL"),
        regime_score=80.0
    )

    if not saved:
        return {"success": False, "error": f"Could not create manual alert: {reason}"}

    # Dispatch Telegram message
    try:
        from telegram_engine import send_telegram_message
        msg = (
            f"🚀 <b>MANUAL {scanner_type} BUY ALERT CREATED</b> 🚀\n\n"
            f"📌 <b>Symbol:</b> #{sym_clean}\n"
            f"💰 <b>Entry Price:</b> ₹{entry_price:.2f}\n"
            f"🛑 <b>Stop Loss:</b> ₹{sl_target.get('stop_loss', 0):.2f}\n"
            f"🎯 <b>Target 1:</b> ₹{sl_target.get('target_1', 0):.2f}\n"
            f"🎯 <b>Target 2:</b> ₹{sl_target.get('target_2', 0):.2f}\n"
            f"🎯 <b>Target 3:</b> ₹{sl_target.get('target_3', 0):.2f}\n"
            f"📊 <b>Score:</b> {score_val}/100\n"
            f"👤 <b>Initiated By:</b> {user_id}"
        )
        send_telegram_message(msg, scan_type=scanner_type)
    except Exception as e:
        logger.warning(f"Telegram manual alert notification dispatch warning: {e}")

    return {
        "success": True,
        "alert_id": alert_id,
        "symbol": sym_clean,
        "scanner": scanner_type,
        "entry_price": entry_price,
        "stop_loss": sl_target.get("stop_loss"),
        "target_1": sl_target.get("target_1"),
        "message": f"Manual {scanner_type} alert successfully raised for #{sym_clean} @ ₹{entry_price:.2f}."
    }
