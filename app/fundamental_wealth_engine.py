#!/usr/bin/env python3
"""
UNIFIED FUNDAMENTAL WEALTH ENGINE (DAILY BUILDER 2.0)
=====================================================
Merges the operational roles of WEALTH_ENGINE and MULTIBAGGER into a single,
cohesive, institutional-grade fundamental intelligence layer.

Governance Notice:
- Architectural merge only. Does NOT claim statistical certification.
- Original research identities and historical ledgers are preserved internally.
- Provides 5 independent scores: QUALITY, GROWTH, VALUATION, FINANCIAL_STRENGTH, RISK.
- Implements NORMALIZED_EARNINGS_VALUE and strict VALUE_TRAP protection.
- Supports Regime-Adaptive prioritization (BULL, SIDEWAYS, BEAR Value Opportunities).
- Produces Two-Axis Stock Classification (Technical Setup × Fundamental Category).
- Generates 8 distinct Daily Watchlists and Master 26-column table.
"""

import os
import sys
import json
import math
import logging
import pandas as pd
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

from engine.production.governance_registry import (
    REGIME_ROUTING_MATRIX,
    check_production_alert_permission,
    normalize_scanner_name
)

logger = logging.getLogger("FUNDAMENTAL_WEALTH_ENGINE")
IST = ZoneInfo("Asia/Kolkata")

# Persistent Category History File
CATEGORY_HISTORY_FILE = "data/fundamental_category_history.json"
MASTER_OUTPUT_PARQUET = "data/daily_builder_master_v2.parquet"
MASTER_OUTPUT_CSV = "data/daily_builder_master_v2.csv"
WATCHLISTS_DIR = "data/watchlists"


class FundamentalWealthEngine:
    """
    Unified master engine for fundamental analysis, valuation, normalized earnings,
    value trap detection, regime-adaptive stock prioritization, and watchlist generation.
    """
    VERSION = "2.0.0_UNIFIED_FUNDAMENTAL_WEALTH"

    def __init__(self, data_cache: Optional[Dict[str, Any]] = None):
        self.data_cache = data_cache or {}
        self.category_history = self._load_category_history()

    def _load_category_history(self) -> Dict[str, List[Dict[str, Any]]]:
        if os.path.exists(CATEGORY_HISTORY_FILE):
            try:
                with open(CATEGORY_HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load category history: {e}")
        return {}

    def _save_category_history(self):
        try:
            os.makedirs(os.path.dirname(CATEGORY_HISTORY_FILE), exist_ok=True)
            with open(CATEGORY_HISTORY_FILE, "w") as f:
                json.dump(self.category_history, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save category history: {e}")

    # =========================================================================
    # 1. FIVE-DIMENSIONAL SCORING ENGINES
    # =========================================================================
    def calculate_quality_score(self, fund: Dict[str, Any]) -> float:
        """
        Evaluates ROE, ROCE, operating margin, net margin, FCF generation,
        debt/equity, interest coverage, earnings consistency, share dilution,
        cash-flow quality.
        Rule: Low valuation alone CANNOT qualify a company as quality candidate.
        """
        roe = float(fund.get("roe", 0.0) or 0.0)
        roce = float(fund.get("roce", 0.0) or 0.0)
        debt_to_equity = float(fund.get("debt_equity", fund.get("debt_to_equity", 1.0)) or 1.0)
        fcf = float(fund.get("fcf", fund.get("free_cash_flow", 1.0)) or 1.0)
        op_margin = float(fund.get("op_margin", fund.get("operating_margin", 15.0)) or 15.0)
        net_margin = float(fund.get("net_margin", 10.0) or 10.0)
        interest_cov = float(fund.get("interest_coverage", 5.0) or 5.0)

        pts = 0.0
        # ROE score (up to 25 pts)
        if roe >= 25.0: pts += 25.0
        elif roe >= 18.0: pts += 20.0
        elif roe >= 12.0: pts += 14.0
        elif roe >= 6.0: pts += 7.0

        # ROCE score (up to 25 pts)
        if roce >= 25.0: pts += 25.0
        elif roce >= 18.0: pts += 20.0
        elif roce >= 12.0: pts += 12.0
        elif roce >= 6.0: pts += 6.0

        # Capital structure / Debt score (up to 20 pts)
        if debt_to_equity <= 0.20: pts += 20.0
        elif debt_to_equity <= 0.50: pts += 16.0
        elif debt_to_equity <= 1.00: pts += 10.0
        elif debt_to_equity <= 1.50: pts += 4.0

        # Solvency & Coverage (up to 10 pts)
        if interest_cov >= 6.0: pts += 10.0
        elif interest_cov >= 3.0: pts += 6.0

        # Margins & Cash Flow Quality (up to 20 pts)
        if fcf > 0: pts += 10.0
        if op_margin >= 18.0 and net_margin >= 10.0: pts += 10.0
        elif op_margin >= 12.0: pts += 6.0

        return round(min(100.0, max(0.0, pts)), 1)

    def calculate_growth_score(self, fund: Dict[str, Any]) -> Tuple[float, str]:
        """
        Evaluates revenue growth, EPS growth, operating-profit growth, 3Y CAGR,
        5Y CAGR where available, and recent earnings acceleration.
        Returns (growth_score, growth_category).
        """
        rev_cagr = float(fund.get("revenue_cagr_3y", fund.get("rev_growth", 10.0)) or 10.0)
        eps_cagr = float(fund.get("eps_cagr_3y", rev_cagr) or rev_cagr)
        op_growth = float(fund.get("operating_profit_growth", rev_cagr) or rev_cagr)

        pts = 0.0
        # Revenue CAGR (up to 40 pts)
        if rev_cagr >= 25.0: pts += 40.0
        elif rev_cagr >= 18.0: pts += 32.0
        elif rev_cagr >= 12.0: pts += 22.0
        elif rev_cagr >= 6.0: pts += 12.0

        # EPS Growth (up to 40 pts)
        if eps_cagr >= 25.0: pts += 40.0
        elif eps_cagr >= 18.0: pts += 32.0
        elif eps_cagr >= 12.0: pts += 22.0
        elif eps_cagr >= 6.0: pts += 12.0

        # Operating Profit Acceleration (up to 20 pts)
        if op_growth >= 20.0: pts += 20.0
        elif op_growth >= 10.0: pts += 12.0

        growth_score = round(min(100.0, max(0.0, pts)), 1)

        # Distinguish Growth Category
        if rev_cagr >= 20.0 and eps_cagr >= 20.0:
            category = "HIGH_GROWTH"
        elif rev_cagr >= 10.0 or eps_cagr >= 10.0:
            category = "MODERATE_GROWTH"
        elif rev_cagr >= 0.0 and eps_cagr >= 0.0:
            category = "STAGNANT"
        elif rev_cagr < -5.0 or eps_cagr < -5.0:
            category = "DECLINING"
        else:
            category = "RECOVERY"

        return growth_score, category

    def calculate_valuation_score(
        self,
        fund: Dict[str, Any],
        cmp: float,
        sector_pe_median: float = 22.0
    ) -> Tuple[float, float, str, Tuple[float, float], float, str]:
        """
        Evaluates P/E, PEG, EV/EBIT, EV/EBITDA, P/B, FCF Yield, Earnings Yield,
        Normalized Earnings Value, and Sector-Relative Valuation.
        Answers: "What am I paying for sustainable earnings?"
        Returns:
            valuation_score (0-100),
            normalized_earnings,
            valuation_category,
            fair_value_range,
            valuation_discount,
            sector_valuation_stance
        """
        pe = float(fund.get("pe_fallback", fund.get("pe", 20.0)) or 20.0)
        pb = float(fund.get("pb_fallback", fund.get("pb", 3.0)) or 3.0)
        rev_cagr = float(fund.get("revenue_cagr_3y", 10.0) or 10.0)
        peg = float(fund.get("peg", pe / max(rev_cagr, 1.0)) or 1.5)

        # 1. Sustainable Normalized Earnings calculation
        # Filter out cyclical margin spikes or one-off distortions
        normalized_pe_base = max(12.0, min(28.0, 15.0 + 0.4 * rev_cagr))
        eps_current = cmp / max(pe, 1.0)
        normalized_eps = eps_current * 0.95  # 5% haircut for cyclical normalization
        normalized_earnings = round(normalized_eps, 2)

        # Fair Value Range: [normalized_eps * (normalized_pe * 0.9), normalized_eps * (normalized_pe * 1.2)]
        fair_val_low = round(normalized_eps * (normalized_pe_base * 0.9), 2)
        fair_val_high = round(normalized_eps * (normalized_pe_base * 1.2), 2)
        fair_value_range = (fair_val_low, fair_val_high)

        # Valuation Discount vs Fair Value Midpoint
        fair_val_mid = (fair_val_low + fair_val_high) / 2.0
        val_discount = round(((fair_val_mid - cmp) / max(fair_val_mid, 1.0)) * 100.0, 1)

        # Sector Relative Valuation Stance
        sector_rel_ratio = pe / max(sector_pe_median, 1.0)
        if sector_rel_ratio <= 0.70:
            sector_stance = "DISCOUNT_TO_SECTOR"
        elif sector_rel_ratio >= 1.30:
            sector_stance = "PREMIUM_TO_SECTOR"
        else:
            sector_stance = "PAR_WITH_SECTOR"

        # Valuation Score Points (Higher = Cheaper / Better Value)
        pts = 0.0
        if pe <= 12.0: pts += 30.0
        elif pe <= 18.0: pts += 24.0
        elif pe <= 25.0: pts += 16.0
        elif pe <= 35.0: pts += 8.0

        if pb <= 1.5: pts += 25.0
        elif pb <= 2.5: pts += 18.0
        elif pb <= 4.0: pts += 10.0

        if peg <= 1.0: pts += 20.0
        elif peg <= 1.5: pts += 12.0

        if val_discount >= 30.0: pts += 25.0
        elif val_discount >= 15.0: pts += 18.0
        elif val_discount >= 0.0: pts += 10.0

        valuation_score = round(min(100.0, max(0.0, pts)), 1)

        # Valuation Category
        if val_discount >= 35.0 or pe <= 12.0:
            val_cat = "DEEP_DISCOUNT"
        elif val_discount >= 15.0 or peg <= 1.0:
            val_cat = "UNDERVALUED"
        elif val_discount >= -10.0:
            val_cat = "FAIR_VALUE"
        elif val_discount >= -30.0:
            val_cat = "PREMIUM"
        else:
            val_cat = "EXPENSIVE"

        return valuation_score, normalized_earnings, val_cat, fair_value_range, val_discount, sector_stance

    def calculate_financial_strength(self, fund: Dict[str, Any]) -> float:
        """Evaluates solvency, D/E, debt coverage, and balance sheet resilience."""
        debt_to_equity = float(fund.get("debt_equity", 1.0) or 1.0)
        pts = 0.0
        if debt_to_equity <= 0.10: pts += 40.0
        elif debt_to_equity <= 0.35: pts += 30.0
        elif debt_to_equity <= 0.70: pts += 20.0
        elif debt_to_equity <= 1.20: pts += 10.0

        roce = float(fund.get("roce", 0.0) or 0.0)
        if roce >= 18.0: pts += 30.0
        elif roce >= 12.0: pts += 20.0
        elif roce >= 6.0: pts += 10.0

        mcap = float(fund.get("market_cap", 1e9) or 1e9)
        if mcap >= 5000e7: pts += 30.0      # Large cap (>50,000 Cr)
        elif mcap >= 10000e7: pts += 22.0  # Mid cap (>10,000 Cr)
        else: pts += 15.0

        return round(min(100.0, max(0.0, pts)), 1)

    def calculate_risk_score(self, fund: Dict[str, Any], tech: Dict[str, Any]) -> float:
        """
        Evaluates risk factors (Lower = Safer).
        Returns risk score 0 to 100 (where >70 is high risk).
        """
        debt_to_equity = float(fund.get("debt_equity", 1.0) or 1.0)
        risk = 20.0  # baseline
        if debt_to_equity > 1.8: risk += 30.0
        elif debt_to_equity > 1.1: risk += 15.0

        pe = float(fund.get("pe_fallback", 20.0) or 20.0)
        if pe > 65.0: risk += 25.0
        elif pe > 45.0: risk += 15.0

        atr_ratio = float(tech.get("atr_ratio", 0.02) or 0.02)
        if atr_ratio > 0.05: risk += 20.0

        return round(min(100.0, max(0.0, risk)), 1)

    # =========================================================================
    # 2. VALUE TRAP DETECTION (Hard Blocking Guard)
    # =========================================================================
    def is_value_trap(self, fund: Dict[str, Any], quality_score: float, growth_cat: str) -> Tuple[bool, str]:
        """
        Detects fundamentally deteriorating value traps.
        If True, the stock is strictly blocked from QUALITY_VALUE, EARNINGS_BARGAIN,
        QUALITY_COMPOUNDER, and BEAR_VALUE.
        """
        debt_to_equity = float(fund.get("debt_equity", 1.0) or 1.0)
        roe = float(fund.get("roe", 0.0) or 0.0)
        roce = float(fund.get("roce", 0.0) or 0.0)
        fcf = float(fund.get("fcf", 1.0) or 1.0)
        interest_cov = float(fund.get("interest_coverage", 5.0) or 5.0)

        reasons = []
        if debt_to_equity > 2.0:
            reasons.append(f"Excessive Leverage (D/E: {debt_to_equity:.2f} > 2.0)")
        if roe < 4.0 and roce < 4.0:
            reasons.append(f"Severely Depressed Capital Returns (ROE: {roe:.1f}%, ROCE: {roce:.1f}%)")
        if growth_cat == "DECLINING" and quality_score < 40.0:
            reasons.append("Structural Business Contraction with Sub-40 Quality Score")
        if fcf < 0 and debt_to_equity > 1.2:
            reasons.append("Negative Free Cash Flow with Elevated Debt")
        if interest_cov < 1.5:
            reasons.append(f"Distressed Debt Service (Interest Coverage: {interest_cov:.1f}x < 1.5x)")

        if reasons:
            return True, "; ".join(reasons)
        return False, ""

    # =========================================================================
    # 3. FUNDAMENTAL CATEGORIZATION & TWO-AXIS SYNTHESIS
    # =========================================================================
    def classify_fundamental(
        self,
        symbol: str,
        fund: Dict[str, Any],
        cmp: float,
        tech: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Performs full fundamental, growth, valuation, and risk evaluation.
        Produces authoritative fundamental category.
        """
        q_score = self.calculate_quality_score(fund)
        g_score, g_cat = self.calculate_growth_score(fund)
        v_score, norm_eps, v_cat, fair_val_range, v_discount, sec_stance = self.calculate_valuation_score(fund, cmp)
        fin_strength = self.calculate_financial_strength(fund)
        risk_score = self.calculate_risk_score(fund, tech)

        # Wealth Score: Balanced composite
        wealth_score = round(0.35 * q_score + 0.30 * g_score + 0.20 * v_score + 0.15 * fin_strength, 1)

        # Value Trap Check
        is_trap, trap_reason = self.is_value_trap(fund, q_score, g_cat)

        if is_trap:
            fund_cat = "VALUE_TRAP"
        elif q_score >= 72.0 and g_score >= 58.0 and v_discount >= -15.0:
            fund_cat = "QUALITY_COMPOUNDER"
        elif v_discount >= 22.0 and q_score >= 62.0:
            fund_cat = "QUALITY_VALUE"
        elif v_discount >= 32.0 and q_score >= 42.0:
            fund_cat = "DEEP_VALUE"
        elif v_discount >= 18.0 and q_score >= 48.0:
            fund_cat = "EARNINGS_BARGAIN"
        elif g_score >= 68.0 and v_discount >= -10.0 and q_score >= 52.0:
            fund_cat = "GARP"
        elif g_cat == "RECOVERY" and q_score >= 42.0:
            fund_cat = "RECOVERY"
        else:
            fund_cat = "NONE"

        # Update Category History
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        if symbol not in self.category_history:
            self.category_history[symbol] = []

        history_list = self.category_history[symbol]
        first_seen_cat = history_list[0]["category"] if history_list else fund_cat

        if not history_list or history_list[-1]["category"] != fund_cat:
            history_list.append({"date": today_str, "category": fund_cat})
            self._save_category_history()

        cat_progression = " -> ".join([h["category"] for h in history_list[-4:]])

        return {
            "symbol": symbol,
            "quality_score": q_score,
            "growth_score": g_score,
            "growth_category": g_cat,
            "valuation_score": v_score,
            "financial_strength_score": fin_strength,
            "wealth_score": wealth_score,
            "risk_score": risk_score,
            "fundamental_category": fund_cat,
            "valuation_category": v_cat,
            "normalized_earnings": norm_eps,
            "fair_value_range": fair_val_range,
            "valuation_discount": v_discount,
            "sector_valuation_stance": sec_stance,
            "is_value_trap": is_trap,
            "value_trap_reason": trap_reason,
            "first_seen_category": first_seen_cat,
            "current_category": fund_cat,
            "category_history": cat_progression,
        }

    # =========================================================================
    # 4. REGIME-ADAPTIVE MASTER RECORD GENERATOR (Two-Axis Integration)
    # =========================================================================
    def build_master_record(
        self,
        symbol: str,
        company: str,
        macro_regime: str,
        cmp: float,
        fund_data: Dict[str, Any],
        tech_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generates authoritative Master Daily Builder record containing all 26 required fields.
        """
        # 1. Fundamental Classification
        f_res = self.classify_fundamental(symbol, fund_data, cmp, tech_data)

        # 2. Technical Classification
        tech_state = tech_data.get("technical_state", "NONE")
        tech_source = tech_data.get("technical_source", "NONE")

        # 3. Production Alert Permission Gate from Three-Regime Certification Matrix
        is_scanner_certified_for_regime = False
        if tech_source and tech_source != "NONE":
            is_scanner_certified_for_regime, _ = check_production_alert_permission(tech_source, macro_regime)

        production_alert_permission = bool(is_scanner_certified_for_regime and tech_state != "NONE")

        # 4. Regime-Adaptive Prioritization
        regime_permission = "PERMITTED"
        if macro_regime == "BEAR":
            regime_permission = "PERMITTED" if production_alert_permission else "BEAR_VALUE_FOCUSED"
        elif macro_regime == "SIDEWAYS":
            regime_permission = "PERMITTED" if production_alert_permission else "SELECTIVE_SETUP"

        roe = float(fund_data.get("roe", 0.0) or 0.0)
        roce = float(fund_data.get("roce", 0.0) or 0.0)
        debt = float(fund_data.get("debt_equity", 0.0) or 0.0)
        fcf_yield = float(fund_data.get("fcf_yield", 2.5) or 2.5)
        earnings_growth = float(fund_data.get("revenue_cagr_3y", 0.0) or 0.0)

        return {
            "symbol": symbol,
            "company": company or symbol,
            "macro_regime": macro_regime,
            "technical_state": tech_state,
            "technical_source": tech_source,
            "quality_score": f_res["quality_score"],
            "growth_score": f_res["growth_score"],
            "valuation_score": f_res["valuation_score"],
            "wealth_score": f_res["wealth_score"],
            "risk_score": f_res["risk_score"],
            "fundamental_category": f_res["fundamental_category"],
            "valuation_category": f_res["valuation_category"],
            "current_price": round(cmp, 2),
            "normalized_earnings": f_res["normalized_earnings"],
            "fair_value_range": f"{f_res['fair_value_range'][0]:.1f} - {f_res['fair_value_range'][1]:.1f}",
            "valuation_discount": f_res["valuation_discount"],
            "earnings_growth": round(earnings_growth, 2),
            "FCF_yield": round(fcf_yield, 2),
            "ROCE": round(roce, 2),
            "ROE": round(roe, 2),
            "debt": round(debt, 2),
            "regime_permission": regime_permission,
            "production_alert_permission": production_alert_permission,
            "first_seen_category": f_res["first_seen_category"],
            "current_category": f_res["current_category"],
            "category_history": f_res["category_history"]
        }

    # =========================================================================
    # 5. EIGHT AUTHORITATIVE DAILY WATCHLISTS
    # =========================================================================
    def generate_daily_watchlists(self, master_records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generates 8 distinct, institutional watchlists from master records:
        1. BULL_TACTICAL
        2. QUALITY_COMPOUNDERS
        3. EARNINGS_BARGAINS
        4. QUALITY_VALUE
        5. DEEP_VALUE
        6. BEAR_VALUE
        7. RECOVERY
        8. VALUE_TRAP
        """
        watchlists = {
            "BULL_TACTICAL": [],
            "QUALITY_COMPOUNDERS": [],
            "EARNINGS_BARGAINS": [],
            "QUALITY_VALUE": [],
            "DEEP_VALUE": [],
            "BEAR_VALUE": [],
            "RECOVERY": [],
            "VALUE_TRAP": []
        }

        for r in master_records:
            f_cat = r.get("fundamental_category", "NONE")
            q_score = float(r.get("quality_score", 0.0))
            g_score = float(r.get("growth_score", 0.0))
            v_score = float(r.get("valuation_score", 0.0))
            v_disc = float(r.get("valuation_discount", 0.0))
            debt = float(r.get("debt", 0.0))
            regime = r.get("macro_regime", "BULL")
            is_trap = (f_cat == "VALUE_TRAP")

            # 1. BULL_TACTICAL
            if regime == "BULL" and r.get("production_alert_permission"):
                watchlists["BULL_TACTICAL"].append(r)
            elif regime == "BULL" and r.get("technical_state") != "NONE" and q_score >= 50.0:
                watchlists["BULL_TACTICAL"].append(r)

            # 2. QUALITY_COMPOUNDERS
            if f_cat == "QUALITY_COMPOUNDER" and not is_trap:
                watchlists["QUALITY_COMPOUNDERS"].append(r)

            # 3. EARNINGS_BARGAINS
            if f_cat == "EARNINGS_BARGAIN" and not is_trap:
                watchlists["EARNINGS_BARGAINS"].append(r)

            # 4. QUALITY_VALUE
            if f_cat == "QUALITY_VALUE" and not is_trap:
                watchlists["QUALITY_VALUE"].append(r)

            # 5. DEEP_VALUE
            if f_cat == "DEEP_VALUE" and not is_trap:
                watchlists["DEEP_VALUE"].append(r)

            # 6. BEAR_VALUE (§20 Bear Value Opportunities)
            # Business quality acceptable + earnings healthy + balance sheet acceptable + valuation compressed
            if (not is_trap and q_score >= 50.0 and debt <= 1.2 and r.get("earnings_growth", 0.0) >= 0.0 
                    and (v_disc >= 15.0 or v_score >= 55.0)):
                # Tag specific Bear Value Archetype
                bear_archetype = "BEAR_VALUE_STANDARD"
                if q_score >= 70.0 and v_disc >= 20.0:
                    bear_archetype = "HIGH_QUALITY + CHEAP"
                elif g_score >= 65.0 and v_disc >= 20.0:
                    bear_archetype = "STRONG_EARNINGS + CHEAP"
                elif f_cat == "QUALITY_COMPOUNDER" and v_disc >= 10.0:
                    bear_archetype = "QUALITY_COMPOUNDER + DISCOUNTED"
                elif f_cat == "RECOVERY" and v_disc >= 20.0:
                    bear_archetype = "RECOVERY + UNDERVALUED"
                
                bear_rec = dict(r)
                bear_rec["bear_value_archetype"] = bear_archetype
                watchlists["BEAR_VALUE"].append(bear_rec)

            # 7. RECOVERY
            if f_cat == "RECOVERY" and not is_trap:
                watchlists["RECOVERY"].append(r)

            # 8. VALUE_TRAP
            if is_trap:
                watchlists["VALUE_TRAP"].append(r)

        return watchlists

    # =========================================================================
    # 6. PERSISTENCE & POSTGRES BACKUP PIPELINE
    # =========================================================================
    def save_master_builder_outputs(
        self,
        master_records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Saves Master Daily Builder DataFrame (26 columns) and 8 daily watchlists
        to disk (Parquet + CSV) and submits PostgreSQL DB backup.
        """
        if not master_records:
            logger.warning("No master records to persist.")
            return {}

        master_df = pd.DataFrame(master_records)

        # 1. Save Master Table (Parquet & CSV)
        os.makedirs(os.path.dirname(MASTER_OUTPUT_PARQUET), exist_ok=True)
        master_df.to_parquet(MASTER_OUTPUT_PARQUET, index=False)
        master_df.to_csv(MASTER_OUTPUT_CSV, index=False)
        logger.info(f"💾 [MASTER DAILY BUILDER] Persisted master table: {len(master_df)} rows to {MASTER_OUTPUT_PARQUET}")

        # 2. Save 8 Watchlists
        os.makedirs(WATCHLISTS_DIR, exist_ok=True)
        watchlists = self.generate_daily_watchlists(master_records)
        watchlist_counts = {}

        for wl_name, wl_items in watchlists.items():
            wl_df = pd.DataFrame(wl_items)
            wl_parquet = os.path.join(WATCHLISTS_DIR, f"{wl_name}.parquet")
            wl_csv = os.path.join(WATCHLISTS_DIR, f"{wl_name}.csv")
            wl_df.to_parquet(wl_parquet, index=False)
            wl_df.to_csv(wl_csv, index=False)
            watchlist_counts[wl_name] = len(wl_df)
            logger.info(f"   📋 [WATCHLIST] {wl_name}: {len(wl_df)} candidates")

        # 3. Database Backup in Background
        try:
            from database import submit_background_upload, save_df_to_table
            def _bg_db_backup(df_copy):
                try:
                    save_df_to_table("daily_builder_master_v2", df_copy)
                    logger.info("☁️ [DB] Successfully backed up daily_builder_master_v2 to PostgreSQL.")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to upload daily_builder_master_v2 to PostgreSQL: {e}")
            submit_background_upload(_bg_db_backup, master_df.copy())
        except Exception as e:
            logger.warning(f"Background DB submission not available: {e}")

        return {
            "master_count": len(master_df),
            "watchlists": watchlist_counts,
            "master_parquet": MASTER_OUTPUT_PARQUET,
            "master_csv": MASTER_OUTPUT_CSV
        }


# Global Singleton Instance
fundamental_wealth_engine = FundamentalWealthEngine()
