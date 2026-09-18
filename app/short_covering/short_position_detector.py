"""
app/short_covering/short_position_detector.py

Daily Active F&O Universe Builder & EOD Positioning Engine for Short-Covering Scanner.
Objective:
- Determines ALL ACTIVE F&O equities eligible for the current/next trading session.
- Performs mandatory data and instrument validity checks.
- Prepares and freezes TODAY_FNO_UNIVERSE before 09:05 IST.
- Calculates background open interest and short accumulation context (for analytics & legacy V1 mode).
"""

import os
import logging
import time
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from app.short_covering.fno_universe import fno_universe_manager
from app.short_covering.oi_data_service import oi_data_service
from app.short_covering.short_covering_schema import EODShortPositionCandidate
try:
    from app.lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner
    from app.database import get_connection, upsert_scanner_health, start_scanner_execution_run, complete_scanner_execution_run
    from app.trading_calendar import get_latest_trading_date, is_trading_day
except ImportError:
    from lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner
    from database import get_connection, upsert_scanner_health, start_scanner_execution_run, complete_scanner_execution_run
    from trading_calendar import get_latest_trading_date, is_trading_day

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
_eod_lock = ProcessLock("short_covering_eod_lock")


class ShortPositionDetector:
    """
    Daily Active F&O Universe Builder & EOD Positioning Engine.
    Builds the active F&O universe and provides background positioning analytics.
    """

    def __init__(
        self,
        min_oi_buildup_5d_pct: float = 6.0,
        min_short_buildup_ratio: float = 0.55,
        max_rsi: float = 50.0,
        min_quality_score: float = 0.0,  # C5 Default: No minimum score rejection
        max_watchlist_size: Optional[int] = None  # C5 Default: Uncapped universe
    ):
        self.min_oi_buildup_5d_pct = min_oi_buildup_5d_pct
        self.min_short_buildup_ratio = min_short_buildup_ratio
        self.max_rsi = max_rsi
        self.min_quality_score = min_quality_score
        self.max_watchlist_size = max_watchlist_size

    def scan_eod_universe(
        self,
        as_of: Optional[date] = None,
        custom_symbols: Optional[List[str]] = None,
        persist_db: bool = True,
        trigger_type: str = "SCHEDULED",
        scheduler_name: str = "CRON"
    ) -> List[EODShortPositionCandidate]:
        """
        Scans the F&O universe at EOD to identify stocks with accumulated short positions.
        Returns a list of high-quality candidates sorted by buildup quality score.
        """
        target_date = as_of or datetime.now(IST).date()
        # Resolve to latest valid trading day (e.g. Monday resolves to today or Friday; weekends resolve to Friday)
        valid_trading_date = get_latest_trading_date(target_date) if not is_trading_day(target_date) else target_date

        logger.info("[SHORT_COVERING_EOD] Acquiring lock: short_covering_eod_lock")
        if not _eod_lock.acquire(blocking=False):
            logger.warning("🛑 [SHORT_COVERING_EOD] Lock 'short_covering_eod_lock' held by another instance. Skipping duplicate run.")
            return []

        if custom_symbols:
            symbols = custom_symbols
        else:
            symbols = sorted(list(set(fno_universe_manager.get_fno_symbols())))

        run_ctx = None
        try:
            run_ctx = start_scanner_execution_run(
                scanner_name="SHORT_COVERING_EOD",
                trigger_type=trigger_type,
                scheduler_name=scheduler_name,
                total_stocks=len(symbols)
            )
        except Exception as ctx_err:
            if "already actively running" in str(ctx_err).lower():
                logger.warning("🛑 [SHORT_COVERING_EOD] Already actively running in DB history. Skipping duplicate run.")
                _eod_lock.release()
                return []
            run_ctx = None

        _scan_start = print_scanner_start_banner("SHORT_COVERING_EOD", run_id=run_ctx.run_id if run_ctx else None)
        try:
            import concurrent.futures
            candidates: List[EODShortPositionCandidate] = []
            stale_count = 0
            completed_count = 0

            logger.info("🔍 [SHORT_COVERING_EOD] Scanning %d universe symbols for trading date: %s", len(symbols), valid_trading_date)

            def _eval_worker(sym: str):
                nonlocal completed_count
                cand_res = None
                is_stale_res = False
                _t0_sym = time.monotonic()
                logger.debug("[SC_EOD] ┌── %s ── start eval", sym)
                try:
                    cand_res = self.evaluate_symbol(sym, valid_trading_date)
                    _dur_ms = (time.monotonic() - _t0_sym) * 1000
                    if cand_res is not None:
                        logger.debug(
                            "[SC_EOD] └── %s ── ✅ PASS | score=%.1f | oi_5d=%+.1f%% "
                            "| sbr=%.2f | rsi=%.1f | %.0fms",
                            sym, cand_res.buildup_quality_score,
                            cand_res.oi_buildup_5d_pct,
                            cand_res.short_buildup_ratio, cand_res.rsi_14, _dur_ms,
                        )
                    else:
                        logger.debug("[SC_EOD] └── %s ── SKIP | %.0fms", sym, _dur_ms)
                except Exception as e:
                    logger.warning("[SC_EOD] └── %s ── ERROR during evaluation: %s", sym, e, exc_info=True)
                    is_stale_res = True
                completed_count += 1
                if run_ctx and completed_count % 25 == 0:
                    try:
                        run_ctx.heartbeat()
                    except Exception:
                        pass
                return cand_res, is_stale_res

            max_workers = min(16, max(2, (os.cpu_count() or 4) * 2))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(_eval_worker, symbols))

            for cand, is_stale in results:
                if cand is not None:
                    candidates.append(cand)
                if is_stale:
                    stale_count += 1

            # 25% Stale Data Hard Blocker validation
            from app.market_utils import validate_batch_staleness
            staleness_check = validate_batch_staleness(stale_count, len(symbols), "SHORT_COVERING_EOD", max_stale_pct=25.0, run_ctx=run_ctx)
            if staleness_check["is_blocked"]:
                if run_ctx:
                    complete_scanner_execution_run(run_ctx, status_override="FAILED", stop_reason=staleness_check["error_msg"])
                return []

            # Sort descending by buildup quality score
            candidates.sort(key=lambda c: c.buildup_quality_score, reverse=True)
            logger.info("✅ [SHORT_COVERING_EOD] Identified %d short-buildup candidates for next-day watchlist", len(candidates))

            # Persist to database if requested
            if persist_db:
                self._persist_candidates_to_db(candidates, valid_trading_date)

            dur = round(time.monotonic() - _scan_start, 2)
            if run_ctx:
                run_ctx.set_total_stocks(len(symbols))
                run_ctx.record_fresh_data(len(candidates))
                complete_scanner_execution_run(run_ctx)

            upsert_scanner_health(
                scanner_name="SHORT_COVERING_EOD",
                status="OK",
                outcome="SUCCESS" if len(candidates) > 0 else "ZERO_CANDIDATES",
                total_count=len(symbols),
                processed_count=len(candidates),
                duration_seconds=dur,
                scheduled_for="Daily 09:05 IST (Market Days)",
                run_id=run_ctx.run_id if run_ctx else None
            )
            return candidates
        except Exception as exc:
            dur = round(time.monotonic() - _scan_start, 2)
            logger.exception("❌ [SHORT_COVERING_EOD] Scan failed: %s", exc)
            try:
                from app.database import insert_notification
                insert_notification(
                    notif_type="error",
                    title="🚨 Short Covering EOD Scan Failed",
                    message=f"Exception during 09:05 IST EOD scan: {exc}",
                    symbol=None
                )
            except Exception:
                pass
            if run_ctx:
                complete_scanner_execution_run(run_ctx, exception=exc)
            upsert_scanner_health(
                scanner_name="SHORT_COVERING_EOD",
                status="DOWN",
                outcome="FAILURE",
                error_msg=str(exc),
                duration_seconds=dur,
                scheduled_for="Daily 09:05 IST (Market Days)",
                run_id=run_ctx.run_id if run_ctx else None
            )
            return []
        finally:
            if _scan_start is not None:
                print_scanner_end_banner("SHORT_COVERING_EOD", _scan_start, run_id=run_ctx.run_id if run_ctx else None)
            _eod_lock.release()



    def evaluate_symbol(
        self,
        symbol: str,
        as_of: date
    ) -> Optional[EODShortPositionCandidate]:
        """
        Evaluates a single stock for prior short buildup over a 10-day lookback.
        """
        df = oi_data_service.get_daily_oi_history(symbol, lookback_days=15, as_of=as_of)
        _rows = len(df) if df is not None else 0
        logger.debug("[SC_EOD] %s | [step 1] data_fetch → %d rows (need ≥8)", symbol, _rows)
        if df is None or len(df) < 8:
            logger.debug("[SC_EOD] %s | SKIP — insufficient daily OI history (%d rows)", symbol, _rows)
            return None

        # Sort chronologically
        df = df.sort_values("date").reset_index(drop=True)

        closes = df["close"].values
        total_ois = df["total_oi"].values
        volumes = df["volume"].values

        # 1. Open Interest changes
        cur_oi = total_ois[-1]
        oi_5d_ago = total_ois[-6] if len(total_ois) >= 6 else total_ois[0]
        oi_10d_ago = total_ois[-11] if len(total_ois) >= 11 else total_ois[0]

        oi_5d_pct = ((cur_oi - oi_5d_ago) / max(oi_5d_ago, 1)) * 100.0
        oi_10d_pct = ((cur_oi - oi_10d_ago) / max(oi_10d_ago, 1)) * 100.0
        oi_1d_pct = df["oi_change_pct"].iloc[-1]

        logger.debug(
            "[SC_EOD] %s | [step 2] OI metrics → cur_oi=%d | 1d=%+.2f%% | 5d=%+.2f%% | 10d=%+.2f%%"
            " (thresholds: 5d≥%.1f%%, 10d≥8.0%%)",
            symbol, cur_oi, oi_1d_pct, oi_5d_pct, oi_10d_pct, self.min_oi_buildup_5d_pct,
        )

        # 2. Price changes over 5 and 10 days
        cur_price = closes[-1]
        price_5d_ago = closes[-6] if len(closes) >= 6 else closes[0]
        price_5d_pct = ((cur_price - price_5d_ago) / price_5d_ago) * 100.0

        logger.debug("[SC_EOD] %s | [step 3] price → cur=₹%.2f | 5d_chg=%+.2f%%", symbol, cur_price, price_5d_pct)

        # 3. Short Buildup Ratio (SBR) over last 6-10 days
        # Days where price fell and OI rose
        lookback_slice = df.tail(8)
        price_diffs = lookback_slice["close"].diff().dropna()
        oi_diffs = lookback_slice["total_oi"].diff().dropna()

        short_buildup_days = sum(1 for p, o in zip(price_diffs, oi_diffs) if p < 0 and o > 0)
        total_days = len(price_diffs)
        sbr = short_buildup_days / max(total_days, 1)

        logger.debug(
            "[SC_EOD] %s | [step 4] SBR → short_days=%d/%d | sbr=%.2f (need ≥%.2f)",
            symbol, short_buildup_days, total_days, sbr, self.min_short_buildup_ratio,
        )

        # 4. Technical Indicators (RSI, ATR, Key Levels)
        rsi_14 = self._calculate_rsi(closes)
        atr_14 = self._calculate_atr(df)
        support_level = float(np.min(df["low"].tail(10)))
        overhead_resistance = float(np.max(df["high"].tail(10)))

        logger.debug(
            "[SC_EOD] %s | [step 5] tech → RSI=%.1f (need ≤%.1f) | ATR=%.2f"
            " | support=₹%.2f | resist=₹%.2f",
            symbol, rsi_14, self.max_rsi, atr_14, support_level, overhead_resistance,
        )

        reasons = []
        score = 0.0

        # Criteria checks:
        # A. Prior short accumulation
        _pts_a = 0.0
        if oi_5d_pct >= self.min_oi_buildup_5d_pct or oi_10d_pct >= 8.0:
            _pts_a = 35.0
            reasons.append(f"Strong 5d/10d OI expansion (+{oi_5d_pct:.1f}% / +{oi_10d_pct:.1f}%)")
        elif oi_5d_pct >= 3.0 or oi_10d_pct >= 5.0:
            _pts_a = 20.0
            reasons.append(f"Moderate 5d/10d OI expansion (+{oi_5d_pct:.1f}%)")
        score += _pts_a
        logger.debug(
            "[SC_EOD] %s | [A] OI Buildup → %+.0f pts | (5d=%+.1f%% 10d=%+.1f%% threshold=%.1f%%) | running=%.1f",
            symbol, _pts_a, oi_5d_pct, oi_10d_pct, self.min_oi_buildup_5d_pct, score,
        )

        # B. Short buildup regime (SBR)
        _pts_b = 0.0
        if sbr >= self.min_short_buildup_ratio:
            _pts_b = 25.0
            reasons.append(f"High Short Buildup Ratio ({sbr:.2f})")
        elif sbr >= 0.35 or price_5d_pct < -1.5:
            _pts_b = 15.0
            reasons.append(f"Moderate Short Buildup ({sbr:.2f}) with price drop ({price_5d_pct:.1f}%)")
        score += _pts_b
        logger.debug(
            "[SC_EOD] %s | [B] SBR → %+.0f pts | sbr=%.2f (need ≥%.2f) price_5d=%+.1f%% | running=%.1f",
            symbol, _pts_b, sbr, self.min_short_buildup_ratio, price_5d_pct, score,
        )

        # C. Price in oversold or support-forming zone
        _pts_c = 0.0
        if rsi_14 <= self.max_rsi:
            _pts_c = 20.0
            reasons.append(f"RSI in deep base/oversold zone ({rsi_14:.1f})")
        elif rsi_14 <= 58.0:
            _pts_c = 10.0
            reasons.append(f"Price stabilizing near base (RSI {rsi_14:.1f})")
        score += _pts_c
        logger.debug(
            "[SC_EOD] %s | [C] RSI → %+.0f pts | rsi=%.1f (need ≤%.1f) | running=%.1f",
            symbol, _pts_c, rsi_14, self.max_rsi, score,
        )

        # D. Early signs of short fatigue (OI expansion plateaued or minor 1d dip with green candle)
        _pts_d = 0.0
        _is_green_eod = closes[-1] >= df["open"].iloc[-1]
        if oi_1d_pct <= 0.5 and _is_green_eod:
            _pts_d = 20.0
            reasons.append("1d OI stall/reduction with bullish lower wick")
        score += _pts_d
        logger.debug(
            "[SC_EOD] %s | [D] Fatigue → %+.0f pts | oi_1d=%+.2f%% green=%s | running=%.1f",
            symbol, _pts_d, oi_1d_pct, _is_green_eod, score,
        )

        # RULE 67 RATIONALE: Enforce self.min_quality_score (50.0+) rather than an arbitrary loose
        # 35.0 threshold to prevent low-conviction or noisy synthetic candidates from entering the watchlist.
        if score < self.min_quality_score:
            logger.debug(
                "[SC_EOD] %s | ❌ REJECT | score=%.1f < threshold=%.1f | breakdown: A=%.0f B=%.0f C=%.0f D=%.0f",
                symbol, score, self.min_quality_score, _pts_a, _pts_b, _pts_c, _pts_d,
            )
            if score >= 40.0:
                try:
                    from near_miss_tracker import log_near_miss
                    log_near_miss(
                        symbol=symbol,
                        scanner="SHORT_COVERING_EOD",
                        breakout_type="SHORT_BUILDUP",
                        gate_name="SCORE_BELOW_THRESHOLD",
                        observed_value=round(float(score), 2),
                        threshold_value=float(self.min_quality_score),
                        score=int(score),
                        entry_price=float(cur_price),
                        stop_loss=round(float(cur_price) * 0.95, 2),
                        target_1=round(float(cur_price) * 1.08, 2)
                    )
                except Exception as nm_err:
                    logger.debug("Failed to persist EOD near miss for %s: %s", symbol, nm_err)
            return None

        sector = fno_universe_manager.get_sector(symbol)

        candidate = EODShortPositionCandidate(
            symbol=symbol,
            scan_date=as_of,
            close_price=float(cur_price),
            total_oi=int(cur_oi),
            oi_change_pct_1d=float(oi_1d_pct),
            oi_buildup_5d_pct=float(oi_5d_pct),
            oi_buildup_10d_pct=float(oi_10d_pct),
            short_buildup_ratio=float(sbr),
            rsi_14=float(rsi_14),
            support_level=support_level,
            overhead_resistance=overhead_resistance,
            atr_14=float(atr_14),
            daily_volume=int(volumes[-1]),
            sector=sector,
            buildup_quality_score=min(100.0, score),
            reasons=reasons
        )
        logger.info(
            "[SC_EOD] %s | ✅ CANDIDATE | score=%.1f | oi_5d=%+.1f%% | sbr=%.2f"
            " | rsi=%.1f | close=₹%.2f | sector=%s | reasons=%s",
            symbol, min(100.0, score), oi_5d_pct, sbr, rsi_14, cur_price,
            sector, " | ".join(reasons),
        )
        return candidate

    def _calculate_rsi(self, closes: np.ndarray, period: int = 14) -> float:
        """Calculates RSI over closing prices."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / max(down, 1e-9)
        rsi = 100.0 - 100.0 / (1.0 + rs)

        for i in range(period, len(deltas)):
            delta = deltas[i]
            if delta > 0:
                upval = delta
                downval = 0.0
            else:
                upval = 0.0
                downval = -delta
            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period
            rs = up / max(down, 1e-9)
            rsi = 100.0 - 100.0 / (1.0 + rs)
        return float(rsi)

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculates 14-period Average True Range."""
        if len(df) < 2:
            return float(df["close"].iloc[-1] * 0.02)
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        if len(tr) < period:
            return float(np.mean(tr))
        return float(np.mean(tr[-period:]))

    def _persist_candidates_to_db(self, candidates: List[EODShortPositionCandidate], as_of: date) -> None:
        """Persists next-day short-covering watchlist to database with clean daily refresh and Top-N cap."""
        if not os.getenv("DATABASE_URL") or os.getenv("DISABLE_DB_OI_LOOKUP"):
            return
        
        # RULE 67 RATIONALE: Truncate candidate list to top self.max_watchlist_size (default 35)
        # to ensure intraday Layer 2 monitors only the highest-conviction institutional setups.
        top_candidates = candidates[:self.max_watchlist_size]

        try:
            try:
                from app.database import get_connection
            except ImportError:
                from database import get_connection
            with get_connection(timeout=1) as conn:
                if hasattr(conn, "is_dummy") and conn.is_dummy:
                    return
                with conn.cursor() as cur:
                    # Ensure table exists — create only, no ALTERs here (they follow separately)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS short_covering_watchlist (
                            symbol TEXT NOT NULL,
                            scan_date DATE NOT NULL,
                            close_price DOUBLE PRECISION,
                            total_oi BIGINT,
                            oi_buildup_5d_pct DOUBLE PRECISION,
                            short_buildup_ratio DOUBLE PRECISION,
                            rsi_14 DOUBLE PRECISION,
                            support_level DOUBLE PRECISION,
                            overhead_resistance DOUBLE PRECISION,
                            atr_14 DOUBLE PRECISION,
                            buildup_quality_score DOUBLE PRECISION,
                            sector TEXT,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            PRIMARY KEY (symbol, scan_date)
                        );
                        CREATE INDEX IF NOT EXISTS idx_sc_watchlist_date ON short_covering_watchlist(scan_date);
                    """)
                    # Migrate each numeric column individually with USING cast.
                    # NUMERIC(6,2)->DOUBLE PRECISION requires an explicit USING clause.
                    # Each ALTER is isolated so a single pre-migrated column doesn't abort the rest.
                    for _sc_col in [
                        "close_price", "oi_buildup_5d_pct", "short_buildup_ratio",
                        "rsi_14", "support_level", "overhead_resistance",
                        "atr_14", "buildup_quality_score",
                    ]:
                        try:
                            cur.execute(
                                f"ALTER TABLE short_covering_watchlist "
                                f"ALTER COLUMN {_sc_col} TYPE DOUBLE PRECISION "
                                f"USING {_sc_col}::DOUBLE PRECISION;"
                            )
                        except Exception as _alt_err:
                            logger.debug("short_covering_watchlist ALTER %s (already migrated?): %s", _sc_col, _alt_err)

                    # RULE 67 RATIONALE: Cleanly delete all existing watchlist records for this session date
                    # before inserting the fresh Top-N candidates. This guarantees full idempotency and prevents
                    # stale, rejected, or obsolete stocks from lingering across re-runs.
                    cur.execute("DELETE FROM short_covering_watchlist WHERE scan_date = %s;", (as_of,))

                    for c in top_candidates:
                        cur.execute("""
                            INSERT INTO short_covering_watchlist (
                                symbol, scan_date, close_price, total_oi, oi_buildup_5d_pct,
                                short_buildup_ratio, rsi_14, support_level, overhead_resistance,
                                atr_14, buildup_quality_score, sector
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (symbol, scan_date) DO UPDATE SET
                                close_price = EXCLUDED.close_price,
                                total_oi = EXCLUDED.total_oi,
                                oi_buildup_5d_pct = EXCLUDED.oi_buildup_5d_pct,
                                short_buildup_ratio = EXCLUDED.short_buildup_ratio,
                                rsi_14 = EXCLUDED.rsi_14,
                                support_level = EXCLUDED.support_level,
                                overhead_resistance = EXCLUDED.overhead_resistance,
                                atr_14 = EXCLUDED.atr_14,
                                buildup_quality_score = EXCLUDED.buildup_quality_score,
                                sector = EXCLUDED.sector;
                        """, (
                            c.symbol, c.scan_date, c.close_price, c.total_oi, c.oi_buildup_5d_pct,
                            c.short_buildup_ratio, c.rsi_14, c.support_level, c.overhead_resistance,
                            c.atr_14, c.buildup_quality_score, c.sector
                        ))
                    if hasattr(conn, "commit"):
                        conn.commit()
            logger.info(f"💾 Persisted {len(top_candidates)} top candidates to short_covering_watchlist table (wiped prior records for {as_of})")
        except Exception as e:
            # RULE 67 RATIONALE: Re-raise DB write failure when database is configured so that
            # scanner_health records DOWN / FAILURE instead of a misleading healthy/success state.
            logger.error(f"❌ Database save for short_covering_watchlist failed: {e}")
            if os.getenv("DATABASE_URL") and not os.getenv("DISABLE_DB_OI_LOOKUP"):
                raise



# Global singleton instance
short_position_detector = ShortPositionDetector()
