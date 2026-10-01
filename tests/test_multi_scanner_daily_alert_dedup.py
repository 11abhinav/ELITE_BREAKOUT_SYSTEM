import os
import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo
from app.database import save_alert_if_new, get_connection

IST = ZoneInfo("Asia/Kolkata")


class InMemoryAlertsDB:
    def __init__(self):
        self.alerts = []
        self.next_id = 1

    def cursor(self, *args, **kwargs):
        return InMemoryAlertsCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class InMemoryAlertsCursor:
    def __init__(self, db):
        self.db = db
        self._last_result = None
        self.rowcount = 1

    def execute(self, sql, params=()):
        sql_upper = str(sql or "").upper().strip()
        if "SELECT" in sql_upper and "FROM ALERTS" in sql_upper:
            sym = str(params[0]).strip().upper() if params else ""
            matches = [a for a in self.db.alerts if str(a["symbol"]).upper() == sym]
            if "STATUS = 'OPEN'" in sql_upper or "STATUS IN ('OPEN'" in sql_upper:
                matches = [a for a in matches if a.get("status") in ("OPEN", "ACTIVE")]
            if len(params) >= 5:
                sc_list = [str(params[1]).upper(), str(params[2]).upper()]
                bt_list = [str(params[3]).upper(), str(params[4]).upper()]
                filtered = []
                for a in matches:
                    a_sc = str(a.get("scanner", "")).upper()
                    a_bt = str(a.get("breakout_type", "")).upper()
                    sc_matches = (a_sc in sc_list or a_bt in bt_list or a_sc in bt_list or a_bt in sc_list)
                    if len(params) >= 8:
                        eff_date = str(params[6]).split(" ")[0].split("T")[0]
                        src_date = str(params[7]).split(" ")[0].split("T")[0]
                        a_date = str(a.get("alert_date", "")).split(" ")[0].split("T")[0]
                        a_src = str(a.get("source_trading_date", a_date)).split(" ")[0].split("T")[0]
                        date_matches = (a_date == eff_date or a_src == src_date or a_date == src_date or a_src == eff_date)
                        if sc_matches and date_matches:
                            filtered.append(a)
                    else:
                        if sc_matches:
                            filtered.append(a)
                matches = filtered
            if matches:
                latest = matches[-1]
                if len(params) >= 8:
                    self._last_result = (
                        latest["id"],
                        latest["symbol"],
                        latest["entry_price"],
                        latest.get("stop_loss"),
                        latest.get("target_1"),
                        latest.get("target_2"),
                        latest.get("target_3"),
                        latest.get("signals"),
                        latest.get("score"),
                        latest["alert_date"],
                        latest.get("alert_time"),
                        latest.get("context"),
                        latest.get("trade_evolution_state", "INITIAL"),
                        latest.get("evidence_count", 1),
                        latest.get("distinct_patterns_count", 1),
                        latest.get("scanner"),
                        latest.get("status", "OPEN"),
                        latest.get("breakout_type"),
                        latest.get("source_trading_date", latest["alert_date"])
                    )
                else:
                    self._last_result = (
                        latest["id"],
                        latest["symbol"],
                        latest["entry_price"],
                        latest.get("stop_loss"),
                        latest.get("target_1"),
                        latest["alert_date"],
                        latest.get("alert_time"),
                        latest.get("scanner"),
                        latest.get("status", "OPEN"),
                        latest.get("breakout_type")
                    )
            else:
                self._last_result = None

        elif "INSERT INTO ALERTS" in sql_upper:
            # Check for unique constraint violation: (symbol, breakout_type, scanner, alert_date)
            sym = str(params[0]).strip().upper()
            bt = str(params[1]).strip().upper()
            adate = str(params[3]).split(" ")[0].split("T")[0]
            sc = str(params[6]).strip().upper()
            conflict = any(
                str(a["symbol"]).upper() == sym
                and str(a.get("breakout_type", "")).upper() == bt
                and str(a.get("scanner", "")).upper() == sc
                and str(a.get("alert_date", "")).split(" ")[0].split("T")[0] == adate
                for a in self.db.alerts
            )
            if conflict:
                self._last_result = None
                self.rowcount = 0
            else:
                alert_id = self.db.next_id
                self.db.next_id += 1
                new_alert = {
                    "id": alert_id,
                    "symbol": params[0],
                    "breakout_type": params[1],
                    "alert_time": params[2],
                    "alert_date": str(params[3]),
                    "source_trading_date": str(params[4]),
                    "scanner": params[6],
                    "status": "OPEN",
                    "entry_price": params[8],
                    "stop_loss": params[9],
                    "target_1": params[12],
                    "signals": params[16],
                    "score": params[17],
                }
                self.db.alerts.append(new_alert)
                self._last_result = (alert_id,)
                self.rowcount = 1
        elif "SELECT ID, EVENT_TYPE" in sql_upper:
            self._last_result = None
        elif "INSERT INTO ALERT_EVENTS" in sql_upper:
            self._last_result = (1,)
        elif "INSERT INTO ALERT_OUTCOMES" in sql_upper:
            self._last_result = (1,)
        elif "UPDATE ALERTS" in sql_upper:
            self._last_result = None
        else:
            self._last_result = None
        return self

    def fetchone(self):
        return self._last_result

    def fetchall(self):
        return [self._last_result] if self._last_result else []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestMultiScannerDailyAlertDedup:

    def setup_method(self):
        self.db = InMemoryAlertsDB()

    def test_same_scanner_same_day_duplicate_is_blocked(self):
        """Same scanner on same date must be blocked from re-entering alerts."""
        sym = "TEST_DEDUP_SYM1"
        ins1, reason1, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="TECHNICAL",
            alert_time="2026-08-03 09:30:00",
            scanner="TECHNICAL",
            category="WYCKOFF SPRING TYPE 2",
            entry_price=250.0,
            stop_loss=240.0,
            target_1=270.0,
            signals="WYCKOFF SPRING TYPE 2",
            score=85,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins1 is True
        assert "Inserted" in reason1
        assert len(self.db.alerts) == 1

        # Second call on the same date with same scanner
        ins2, reason2, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="TECHNICAL",
            alert_time="2026-08-03 11:30:00",
            scanner="TECHNICAL",
            category="WYCKOFF SPRING TYPE 2",
            entry_price=250.5,
            stop_loss=240.0,
            target_1=270.0,
            signals="WYCKOFF SPRING TYPE 2",
            score=85,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins2 is False
        assert (
            "BLOCKED" in str(reason2).upper()
            or "OPEN" in str(reason2).upper()
            or "DUPLICATE" in str(reason2).upper()
            or "MATERIAL" in str(reason2).upper()
            or "RE-TRIGGER" in str(reason2).upper()
        )
        # Verify no duplicate row was created in DB
        assert len(self.db.alerts) == 1

    def test_same_scanner_new_day_alert_is_blocked_if_open(self):
        """Same scanner on a new day must be BLOCKED if previous alert is still OPEN."""
        sym = "TEST_DEDUP_SYM2"
        # Day 1 alert (Monday 2026-08-03)
        ins1, reason1, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="TECHNICAL",
            alert_time="2026-08-03 09:30:00",
            scanner="TECHNICAL",
            category="WYCKOFF SPRING TYPE 2",
            entry_price=250.0,
            stop_loss=240.0,
            target_1=270.0,
            signals="WYCKOFF SPRING TYPE 2",
            score=85,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins1 is True
        assert len(self.db.alerts) == 1

        # Day 2 alert (Tuesday 2026-08-04, next day) — MUST BE BLOCKED because Day 1 is still OPEN
        ins2, reason2, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="TECHNICAL",
            alert_time="2026-08-04 09:30:00",
            scanner="TECHNICAL",
            category="WYCKOFF SPRING TYPE 2",
            entry_price=255.0,
            stop_loss=245.0,
            target_1=275.0,
            signals="WYCKOFF SPRING TYPE 2",
            score=88,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins2 is False
        assert "OPEN" in str(reason2).upper()
        # Verify DB still has only 1 alert for this symbol
        assert len(self.db.alerts) == 1

        # Now simulate Day 1 position being closed (e.g. exit reached)
        self.db.alerts[0]["status"] = "CLOSED"

        # Day 3 alert (Wednesday 2026-08-05) after Day 1 is CLOSED — MUST BE ALLOWED
        ins3, reason3, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="TECHNICAL",
            alert_time="2026-08-05 09:30:00",
            scanner="TECHNICAL",
            category="WYCKOFF SPRING TYPE 2",
            entry_price=260.0,
            stop_loss=250.0,
            target_1=280.0,
            signals="WYCKOFF SPRING TYPE 2",
            score=90,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins3 is True
        assert "Inserted" in reason3
        assert len(self.db.alerts) == 2

    def test_different_scanners_same_day_are_allowed(self):
        """Different scanners must both be allowed to alert on the same day for the same symbol."""
        sym = "TEST_DEDUP_SYM3"
        # Scanner 1: TECHNICAL at 09:30 AM
        ins1, reason1, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="TECHNICAL",
            alert_time="2026-08-03 09:30:00",
            scanner="TECHNICAL",
            category="WYCKOFF SPRING TYPE 2",
            entry_price=500.0,
            stop_loss=480.0,
            target_1=540.0,
            signals="WYCKOFF SPRING TYPE 2",
            score=85,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins1 is True

        # Scanner 2: FUNDAMENTAL at 15:30 PM on the same date
        ins2, reason2, _, _ = save_alert_if_new(
            symbol=sym,
            breakout_type="FUNDAMENTAL_BREAKOUT",
            alert_time="2026-08-03 15:30:00",
            scanner="FUNDAMENTAL",
            category="OPEN_TARGET / WEALTH_EXIT_V1",
            entry_price=502.0,
            stop_loss=None,
            target_1=None,
            signals="FUNDAMENTAL QUALITY + 20D BREAKOUT",
            score=95,
            bayesian_regime="BULL",
            conn=self.db,
        )
        assert ins2 is True

        # Verify DB has 2 alerts for this symbol on the same day from different scanners
        assert len(self.db.alerts) == 2
        scanners = {a["scanner"] for a in self.db.alerts}
        assert scanners == {"TECHNICAL", "FUNDAMENTAL"}

    def test_all_scanners_can_alert_on_new_day(self):
        """All scanners must be allowed to alert on a new day, and duplicates on the new day must be blocked."""
        sym = "TEST_DEDUP_SYM4"
        # Day 1: TECHNICAL and FUNDAMENTAL alert
        ins1, _, _, _ = save_alert_if_new(
            symbol=sym, breakout_type="TECHNICAL", alert_time="2026-08-03 09:30:00",
            scanner="TECHNICAL", category="WYCKOFF SPRING TYPE 2", entry_price=100.0,
            stop_loss=95.0, target_1=110.0, score=85, bayesian_regime="BULL", conn=self.db,
        )
        assert ins1 is True

        ins2, _, _, _ = save_alert_if_new(
            symbol=sym, breakout_type="FUNDAMENTAL_BREAKOUT", alert_time="2026-08-03 15:30:00",
            scanner="FUNDAMENTAL", category="OPEN_TARGET", entry_price=101.0,
            score=95, bayesian_regime="BULL", conn=self.db,
        )
        assert ins2 is True
        assert len(self.db.alerts) == 2

        # Day 2: Both TECHNICAL and FUNDAMENTAL alerts are BLOCKED because Day 1 is still OPEN
        ins3, reason3, _, _ = save_alert_if_new(
            symbol=sym, breakout_type="TECHNICAL", alert_time="2026-08-04 09:30:00",
            scanner="TECHNICAL", category="WYCKOFF SPRING TYPE 2", entry_price=105.0,
            stop_loss=100.0, target_1=115.0, score=88, bayesian_regime="BULL", conn=self.db,
        )
        assert ins3 is False
        assert "OPEN" in str(reason3).upper()

        ins4, reason4, _, _ = save_alert_if_new(
            symbol=sym, breakout_type="FUNDAMENTAL_BREAKOUT", alert_time="2026-08-04 15:30:00",
            scanner="FUNDAMENTAL", category="OPEN_TARGET", entry_price=106.0,
            score=96, bayesian_regime="BULL", conn=self.db,
        )
        assert ins4 is False
        assert "OPEN" in str(reason4).upper()
        assert len(self.db.alerts) == 2

        # Once Day 1 positions are CLOSED, both scanners can alert again on the new day
        for a in self.db.alerts:
            a["status"] = "CLOSED"

        ins5, _, _, _ = save_alert_if_new(
            symbol=sym, breakout_type="TECHNICAL", alert_time="2026-08-04 09:30:00",
            scanner="TECHNICAL", category="WYCKOFF SPRING TYPE 2", entry_price=105.0,
            stop_loss=100.0, target_1=115.0, score=88, bayesian_regime="BULL", conn=self.db,
        )
        assert ins5 is True

        ins6, _, _, _ = save_alert_if_new(
            symbol=sym, breakout_type="FUNDAMENTAL_BREAKOUT", alert_time="2026-08-04 15:30:00",
            scanner="FUNDAMENTAL", category="OPEN_TARGET", entry_price=106.0,
            score=96, bayesian_regime="BULL", conn=self.db,
        )
        assert ins6 is True
        assert len(self.db.alerts) == 4
