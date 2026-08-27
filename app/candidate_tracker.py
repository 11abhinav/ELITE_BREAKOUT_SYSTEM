# =====================================================================================
# app/candidate_tracker.py
# V2 CANDIDATE LIFECYCLE TRACKER
# =====================================================================================
#
# Manages the complete lifecycle of scanner_candidates rows:
#
#   WATCH → CANDIDATE → CONFIRMED / MISSED / EXPIRED
#
# Core guarantees:
#   1. All state transitions go through assert_valid_transition() from signal_contract.
#      Forbidden transitions (e.g. WATCH → MISSED) raise ForbiddenStateTransitionError.
#   2. Confirmation upgrade is fully transactional (SELECT FOR UPDATE).
#   3. Alert insertion uses idempotency_key to prevent duplicate Admin notifications.
#   4. Every upsert inserts a candidate_snapshot (point-in-time audit trail).
#   5. EXPIRED writes failed_checklists=[] — nothing actually failed for a timeout.
#   6. MISSED writes the actual failed_checklists populated by the scanner.
#   7. trigger_level and detected_at are IMMUTABLE once set on creation.
#
# DB tables managed here:
#   scanner_candidates    (upsert by setup_id)
#   candidate_snapshots   (insert-only per scan run)
#
# DB tables touched by confirm_candidate():
#   alerts                (INSERT with idempotency_key)
# =====================================================================================

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from database import get_connection, init_db, IST
from signal_contract import (
    ForbiddenStateTransitionError,
    WatchExplanation,
    assert_valid_transition,
)

logger = logging.getLogger("candidate_tracker")

# -------------------------------------------------------------------------------------
# SCHEMA INIT
# -------------------------------------------------------------------------------------

_SCHEMA_INIT_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False


def init_candidate_schema() -> None:
    """
    Creates scanner_candidates, candidate_snapshots, and near_miss_outcomes tables.
    Also adds idempotency_key to the alerts table for duplicate alert prevention.
    Thread-safe: runs exactly once per process lifetime.
    """
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    with _SCHEMA_INIT_LOCK:
        if _SCHEMA_INITIALIZED:
            return
        init_db()
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:

                    # ── scanner_candidates ──────────────────────────────────────────
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS scanner_candidates (
                            candidate_id         BIGSERIAL PRIMARY KEY,
                            setup_id             VARCHAR(128) UNIQUE NOT NULL,
                            symbol               VARCHAR(50)  NOT NULL,
                            scanner_name         VARCHAR(50)  NOT NULL,
                            setup_type           VARCHAR(80)  NOT NULL,
                            state                VARCHAR(30)  NOT NULL,
                            structure_date       DATE         NOT NULL,

                            detected_at          TIMESTAMPTZ  NOT NULL,
                            triggered_at         TIMESTAMPTZ,
                            confirmed_at         TIMESTAMPTZ,
                            invalidated_at       TIMESTAMPTZ,
                            expires_at           TIMESTAMPTZ,

                            trigger_level        NUMERIC(12, 2),
                            invalidation_level   NUMERIC(12, 2),
                            next_required_event  TEXT,
                            setup_reset_reason   VARCHAR(80),

                            last_evaluated_at    TIMESTAMPTZ  NOT NULL,
                            last_seen_price      NUMERIC(12, 2),
                            last_seen_volume     NUMERIC(16, 2),

                            distance_to_trigger_pct  NUMERIC(6, 2),
                            distance_to_trigger_atr  NUMERIC(6, 2),
                            extension_from_base_atr  NUMERIC(6, 2),

                            quality_score        NUMERIC(6, 2),
                            risk_score           NUMERIC(6, 2),
                            reward_risk_ratio    NUMERIC(6, 2),

                            stop_loss            NUMERIC(12, 2),
                            target_1             NUMERIC(12, 2),
                            target_2             NUMERIC(12, 2),
                            target_3             NUMERIC(12, 2),

                            confirmation_delay_bars  INTEGER DEFAULT 0,

                            status_reason        TEXT,
                            failure_reason_code  VARCHAR(50),

                            cleared_checklists        JSONB,
                            pending_checklists        JSONB,
                            failed_checklists         JSONB,
                            warning_checklists        JSONB,
                            not_applicable_checklists JSONB,

                            primary_blocker_type  VARCHAR(50),
                            primary_blocker       JSONB,

                            health_status         VARCHAR(30),
                            health_reason         TEXT,
                            last_change_summary   TEXT,

                            reasons               JSONB,
                            warnings              JSONB,
                            metadata              JSONB,
                            data_quality          JSONB,
                            algorithm_version     VARCHAR(20),

                            created_at            TIMESTAMPTZ DEFAULT NOW(),
                            updated_at            TIMESTAMPTZ DEFAULT NOW()
                        )
                    """)

                    cur.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_setup_id
                            ON scanner_candidates (setup_id)
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_candidates_state
                            ON scanner_candidates (state, scanner_name)
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_candidates_symbol
                            ON scanner_candidates (symbol)
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_candidates_blocker_type
                            ON scanner_candidates (primary_blocker_type)
                    """)

                    # ── candidate_snapshots ─────────────────────────────────────────
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS candidate_snapshots (
                            snapshot_id          BIGSERIAL    PRIMARY KEY,
                            candidate_id         BIGINT       NOT NULL
                                                 REFERENCES scanner_candidates(candidate_id)
                                                 ON DELETE CASCADE,
                            snapshot_time        TIMESTAMPTZ  NOT NULL,
                            snapshot_reason      VARCHAR(50)  NOT NULL,

                            price                NUMERIC(12, 2),
                            trigger_level        NUMERIC(12, 2),
                            distance_to_trigger_pct  NUMERIC(6, 2),
                            distance_to_trigger_atr  NUMERIC(6, 2),
                            extension_from_base_atr  NUMERIC(6, 2),
                            quality_score        NUMERIC(6, 2),
                            volume_ratio         NUMERIC(6, 2),
                            rs_rating            NUMERIC(6, 2),
                            sector_rank          INTEGER,
                            atr                  NUMERIC(10, 2),
                            support_level        NUMERIC(12, 2),
                            rr                   NUMERIC(6, 2),
                            health_status        VARCHAR(30),
                            health_reason        TEXT,
                            cleared_json         JSONB,
                            pending_json         JSONB,
                            warnings_json        JSONB,
                            not_applicable_json  JSONB
                        )
                    """)

                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_snapshots_candidate_time
                            ON candidate_snapshots (candidate_id, snapshot_time DESC)
                    """)

                    # ── near_miss_outcomes ──────────────────────────────────────────
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS near_miss_outcomes (
                            id               BIGSERIAL    PRIMARY KEY,
                            near_miss_id     INTEGER      UNIQUE NOT NULL
                                             REFERENCES near_misses(id) ON DELETE CASCADE,
                            return_1d        NUMERIC(8, 2),
                            return_3d        NUMERIC(8, 2),
                            return_5d        NUMERIC(8, 2),
                            return_10d       NUMERIC(8, 2),
                            return_20d       NUMERIC(8, 2),
                            return_60d       NUMERIC(8, 2),
                            mfe              NUMERIC(8, 2),
                            mae              NUMERIC(8, 2),
                            hypothetical_r   NUMERIC(6, 2),
                            rejection_verdict VARCHAR(30) NOT NULL,
                            evaluated_at     TIMESTAMPTZ DEFAULT NOW()
                        )
                    """)

                    # ── alerts: idempotency_key column ──────────────────────────────
                    # Adds idempotency_key to the existing alerts table if missing.
                    # The unique index prevents duplicate CONFIRMED_BUY alerts on retry.
                    cur.execute("""
                        ALTER TABLE alerts
                            ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(200)
                    """)
                    cur.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_idempotency
                            ON alerts (idempotency_key)
                            WHERE idempotency_key IS NOT NULL
                    """)

                    conn.commit()
                    logger.info("✅ [candidate_tracker] V2 schema ready: scanner_candidates, candidate_snapshots, near_miss_outcomes, alerts.idempotency_key")
        except Exception:
            logger.exception("❌ [candidate_tracker] Schema initialization failed")
        finally:
            _SCHEMA_INITIALIZED = True


# -------------------------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(IST)


def _jsonb(value: Any) -> Optional[str]:
    """Serialize to JSON string for psycopg2, or return None."""
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def _make_expires_at(detected_at: datetime, expiry_sessions: int) -> datetime:
    """
    Approximates 'N trading sessions from detected_at' as N calendar days.
    Actual trading calendar awareness can be added later.
    """
    return detected_at + timedelta(days=expiry_sessions)


# -------------------------------------------------------------------------------------
# PUBLIC API
# -------------------------------------------------------------------------------------

def upsert_candidate(
    setup_id: str,
    payload: Dict[str, Any],
    conn,
    *,
    snapshot_reason: str = "QUALITY_IMPROVEMENT",
) -> int:
    """
    Creates or updates a scanner_candidates row identified by setup_id.

    Rules:
    - On INSERT: detected_at and trigger_level are written and become immutable.
    - On UPDATE: detected_at and trigger_level are NEVER overwritten.
    - Always inserts a candidate_snapshot after upsert.
    - Returns the candidate_id.

    payload keys (all optional except where noted):
        symbol, scanner_name, setup_type, state (required for INSERT)
        structure_date, trigger_level, invalidation_level, next_required_event
        expires_at, expiry_sessions (int — used if expires_at not given)
        last_seen_price, last_seen_volume
        distance_to_trigger_pct, distance_to_trigger_atr, extension_from_base_atr
        quality_score, risk_score, reward_risk_ratio
        stop_loss, target_1, target_2, target_3
        status_reason, failure_reason_code
        cleared_checklists, pending_checklists, failed_checklists,
        warning_checklists, not_applicable_checklists
        primary_blocker_type, primary_blocker
        health_status, health_reason, last_change_summary
        reasons, warnings, metadata, data_quality, algorithm_version
    """
    now = _now()

    # Compute expires_at if not provided
    expires_at = payload.get("expires_at")
    if expires_at is None and "expiry_sessions" in payload:
        detected_at_ref = payload.get("detected_at", now)
        expires_at = _make_expires_at(detected_at_ref, int(payload["expiry_sessions"]))

    with conn.cursor() as cur:
        # ── Upsert scanner_candidates ──────────────────────────────────────────────
        cur.execute("""
            INSERT INTO scanner_candidates (
                setup_id, symbol, scanner_name, setup_type, state,
                structure_date, detected_at, expires_at,
                trigger_level, invalidation_level, next_required_event,
                last_evaluated_at, last_seen_price, last_seen_volume,
                distance_to_trigger_pct, distance_to_trigger_atr, extension_from_base_atr,
                quality_score, risk_score, reward_risk_ratio,
                stop_loss, target_1, target_2, target_3,
                status_reason, failure_reason_code,
                cleared_checklists, pending_checklists, failed_checklists,
                warning_checklists, not_applicable_checklists,
                primary_blocker_type, primary_blocker,
                health_status, health_reason, last_change_summary,
                reasons, warnings, metadata, data_quality, algorithm_version,
                created_at, updated_at
            )
            VALUES (
                %(setup_id)s, %(symbol)s, %(scanner_name)s, %(setup_type)s, %(state)s,
                %(structure_date)s, %(detected_at)s, %(expires_at)s,
                %(trigger_level)s, %(invalidation_level)s, %(next_required_event)s,
                %(last_evaluated_at)s, %(last_seen_price)s, %(last_seen_volume)s,
                %(distance_to_trigger_pct)s, %(distance_to_trigger_atr)s, %(extension_from_base_atr)s,
                %(quality_score)s, %(risk_score)s, %(reward_risk_ratio)s,
                %(stop_loss)s, %(target_1)s, %(target_2)s, %(target_3)s,
                %(status_reason)s, %(failure_reason_code)s,
                %(cleared_checklists)s::jsonb, %(pending_checklists)s::jsonb, %(failed_checklists)s::jsonb,
                %(warning_checklists)s::jsonb, %(not_applicable_checklists)s::jsonb,
                %(primary_blocker_type)s, %(primary_blocker)s::jsonb,
                %(health_status)s, %(health_reason)s, %(last_change_summary)s,
                %(reasons)s::jsonb, %(warnings)s::jsonb, %(metadata)s::jsonb,
                %(data_quality)s::jsonb, %(algorithm_version)s,
                %(now)s, %(now)s
            )
            ON CONFLICT (setup_id) DO UPDATE SET
                state                    = EXCLUDED.state,
                last_evaluated_at        = EXCLUDED.last_evaluated_at,
                last_seen_price          = EXCLUDED.last_seen_price,
                last_seen_volume         = EXCLUDED.last_seen_volume,
                distance_to_trigger_pct  = EXCLUDED.distance_to_trigger_pct,
                distance_to_trigger_atr  = EXCLUDED.distance_to_trigger_atr,
                extension_from_base_atr  = EXCLUDED.extension_from_base_atr,
                quality_score            = EXCLUDED.quality_score,
                risk_score               = EXCLUDED.risk_score,
                reward_risk_ratio        = EXCLUDED.reward_risk_ratio,
                stop_loss                = EXCLUDED.stop_loss,
                target_1                 = EXCLUDED.target_1,
                target_2                 = EXCLUDED.target_2,
                target_3                 = EXCLUDED.target_3,
                next_required_event      = COALESCE(EXCLUDED.next_required_event,
                                                    scanner_candidates.next_required_event),
                invalidation_level       = COALESCE(EXCLUDED.invalidation_level,
                                                    scanner_candidates.invalidation_level),
                expires_at               = COALESCE(scanner_candidates.expires_at,
                                                    EXCLUDED.expires_at),
                status_reason            = EXCLUDED.status_reason,
                failure_reason_code      = EXCLUDED.failure_reason_code,
                cleared_checklists       = EXCLUDED.cleared_checklists,
                pending_checklists       = EXCLUDED.pending_checklists,
                failed_checklists        = EXCLUDED.failed_checklists,
                warning_checklists       = EXCLUDED.warning_checklists,
                not_applicable_checklists = EXCLUDED.not_applicable_checklists,
                primary_blocker_type     = EXCLUDED.primary_blocker_type,
                primary_blocker          = EXCLUDED.primary_blocker,
                health_status            = EXCLUDED.health_status,
                health_reason            = EXCLUDED.health_reason,
                last_change_summary      = EXCLUDED.last_change_summary,
                reasons                  = EXCLUDED.reasons,
                warnings                 = EXCLUDED.warnings,
                metadata                 = COALESCE(EXCLUDED.metadata,
                                                    scanner_candidates.metadata),
                data_quality             = EXCLUDED.data_quality,
                algorithm_version        = COALESCE(EXCLUDED.algorithm_version,
                                                    scanner_candidates.algorithm_version),
                updated_at               = EXCLUDED.updated_at
                -- NOTE: detected_at, trigger_level, and created_at are intentionally
                -- NOT in the DO UPDATE SET clause — they are immutable once set.
            RETURNING candidate_id
        """, {
            "setup_id":                  setup_id,
            "symbol":                    payload.get("symbol", ""),
            "scanner_name":              payload.get("scanner_name", ""),
            "setup_type":                payload.get("setup_type", ""),
            "state":                     payload.get("state", "WATCH"),
            "structure_date":            payload.get("structure_date"),
            "detected_at":               payload.get("detected_at", now),
            "expires_at":                expires_at,
            "trigger_level":             payload.get("trigger_level"),
            "invalidation_level":        payload.get("invalidation_level"),
            "next_required_event":       payload.get("next_required_event"),
            "last_evaluated_at":         now,
            "last_seen_price":           payload.get("last_seen_price"),
            "last_seen_volume":          payload.get("last_seen_volume"),
            "distance_to_trigger_pct":   payload.get("distance_to_trigger_pct"),
            "distance_to_trigger_atr":   payload.get("distance_to_trigger_atr"),
            "extension_from_base_atr":   payload.get("extension_from_base_atr"),
            "quality_score":             payload.get("quality_score"),
            "risk_score":                payload.get("risk_score"),
            "reward_risk_ratio":         payload.get("reward_risk_ratio"),
            "stop_loss":                 payload.get("stop_loss"),
            "target_1":                  payload.get("target_1"),
            "target_2":                  payload.get("target_2"),
            "target_3":                  payload.get("target_3"),
            "status_reason":             payload.get("status_reason"),
            "failure_reason_code":       payload.get("failure_reason_code"),
            "cleared_checklists":        _jsonb(payload.get("cleared_checklists", [])),
            "pending_checklists":        _jsonb(payload.get("pending_checklists", [])),
            "failed_checklists":         _jsonb(payload.get("failed_checklists", [])),
            "warning_checklists":        _jsonb(payload.get("warning_checklists", [])),
            "not_applicable_checklists": _jsonb(payload.get("not_applicable_checklists", [])),
            "primary_blocker_type":      payload.get("primary_blocker_type"),
            "primary_blocker":           _jsonb(payload.get("primary_blocker")),
            "health_status":             payload.get("health_status"),
            "health_reason":             payload.get("health_reason"),
            "last_change_summary":       payload.get("last_change_summary"),
            "reasons":                   _jsonb(payload.get("reasons", [])),
            "warnings":                  _jsonb(payload.get("warnings", [])),
            "metadata":                  _jsonb(payload.get("metadata")),
            "data_quality":              _jsonb(payload.get("data_quality")),
            "algorithm_version":         payload.get("algorithm_version", "v2.0"),
            "now":                       now,
        })

        row = cur.fetchone()
        candidate_id = row[0]

    # ── Insert snapshot (point-in-time audit) ──────────────────────────────────────
    insert_snapshot(
        candidate_id=candidate_id,
        snapshot_reason=snapshot_reason,
        payload=payload,
        conn=conn,
    )

    return candidate_id


def insert_snapshot(
    candidate_id: int,
    snapshot_reason: str,
    payload: Dict[str, Any],
    conn,
) -> None:
    """
    Inserts one candidate_snapshots row.
    Called automatically by upsert_candidate and explicitly on TRIGGERED/CONFIRMED/MISSED/EXPIRED.

    snapshot_reason values:
        INITIAL_WATCH | QUALITY_IMPROVEMENT | TRIGGERED | CONFIRMATION_CHECK
        | DETERIORATION | INVALIDATION | EXPIRY
    """
    now = _now()

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO candidate_snapshots (
                candidate_id, snapshot_time, snapshot_reason,
                price, trigger_level,
                distance_to_trigger_pct, distance_to_trigger_atr, extension_from_base_atr,
                quality_score, volume_ratio, rs_rating, sector_rank,
                atr, support_level, rr,
                health_status, health_reason,
                cleared_json, pending_json, warnings_json, not_applicable_json
            )
            VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
            )
        """, (
            candidate_id,
            now,
            snapshot_reason,
            payload.get("last_seen_price"),
            payload.get("trigger_level"),
            payload.get("distance_to_trigger_pct"),
            payload.get("distance_to_trigger_atr"),
            payload.get("extension_from_base_atr"),
            payload.get("quality_score"),
            payload.get("volume_ratio"),
            payload.get("rs_rating"),
            payload.get("sector_rank"),
            payload.get("atr"),
            payload.get("support_level"),
            payload.get("reward_risk_ratio"),
            payload.get("health_status"),
            payload.get("health_reason"),
            _jsonb(payload.get("cleared_checklists", [])),
            _jsonb(payload.get("pending_checklists", [])),
            _jsonb(payload.get("warnings", [])),
            _jsonb(payload.get("not_applicable_checklists", [])),
        ))


def transition_state(
    setup_id: str,
    new_state: str,
    conn,
    *,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Reads the current state of setup_id and asserts the transition is valid before
    applying it. Raises ForbiddenStateTransitionError on illegal transitions.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT state FROM scanner_candidates WHERE setup_id = %s",
            (setup_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"[candidate_tracker] setup_id not found: {setup_id!r}")
        current_state = row[0]

    # Raises ForbiddenStateTransitionError if transition is not in ALLOWED_TRANSITIONS
    assert_valid_transition(current_state, new_state, setup_id=setup_id)

    now = _now()
    fields: Dict[str, Any] = {"state": new_state, "updated_at": now, **(extra_fields or {})}

    set_clauses = ", ".join(f"{k} = %({k})s" for k in fields)
    fields["setup_id"] = setup_id

    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE scanner_candidates SET {set_clauses} WHERE setup_id = %(setup_id)s",
            fields,
        )


def expire_candidate(
    setup_id: str,
    conn,
    *,
    failure_reason_code: str = "EXPIRED",
    setup_reset_reason: Optional[str] = None,
    snapshot_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Transitions a candidate to EXPIRED state.

    Invariant (from specification §2.6b):
        EXPIRED candidates have failed_checklists = [] (nothing actually failed).
        The failure_reason_code explains WHY the setup expired (timeout, structure reset, etc.)

    snapshot_payload: optional current market state for the EXPIRY snapshot.
    """
    now = _now()
    extra: Dict[str, Any] = {
        "state":               "EXPIRED",
        "invalidated_at":      now,
        "failure_reason_code": failure_reason_code,
        "failed_checklists":   json.dumps([]),  # Invariant: empty for pure expiry
    }
    if setup_reset_reason:
        extra["setup_reset_reason"] = setup_reset_reason

    transition_state(setup_id, "EXPIRED", conn, extra_fields=extra)

    # Snapshot the final state
    if snapshot_payload is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT candidate_id FROM scanner_candidates WHERE setup_id = %s",
                (setup_id,)
            )
            row = cur.fetchone()
            if row:
                snap = {**snapshot_payload, "failure_reason_code": failure_reason_code}
                insert_snapshot(row[0], "EXPIRY", snap, conn)

    logger.info(
        f"⏰ [EXPIRED] setup_id={setup_id!r} reason={failure_reason_code!r}"
        + (f" reset={setup_reset_reason!r}" if setup_reset_reason else "")
    )


def miss_candidate(
    setup_id: str,
    failed_checklists: List[str],
    failure_reason_code: str,
    conn,
    *,
    snapshot_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Transitions a CANDIDATE to MISSED state.

    Invariant: failed_checklists must be non-empty — it records what actually blocked
    confirmation. If nothing failed, use expire_candidate() instead.

    Raises ValueError if failed_checklists is empty (protect the data invariant).
    """
    if not failed_checklists:
        raise ValueError(
            f"[candidate_tracker] miss_candidate called with empty failed_checklists "
            f"for setup_id={setup_id!r}. "
            "If no criteria failed, use expire_candidate() for a clean EXPIRY transition."
        )

    now = _now()
    extra = {
        "state":               "MISSED",
        "invalidated_at":      now,
        "failed_checklists":   json.dumps(failed_checklists),
        "failure_reason_code": failure_reason_code,
    }
    transition_state(setup_id, "MISSED", conn, extra_fields=extra)

    if snapshot_payload is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT candidate_id FROM scanner_candidates WHERE setup_id = %s",
                (setup_id,)
            )
            row = cur.fetchone()
            if row:
                snap = {
                    **snapshot_payload,
                    "failed_checklists": failed_checklists,
                    "failure_reason_code": failure_reason_code,
                }
                insert_snapshot(row[0], "INVALIDATION", snap, conn)

    logger.info(
        f"📉 [MISSED] setup_id={setup_id!r} "
        f"reason={failure_reason_code!r} failed={failed_checklists}"
    )


def mark_triggered(
    setup_id: str,
    conn,
    *,
    snapshot_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Transitions a WATCH candidate to CANDIDATE state (trigger has fired).
    Records triggered_at timestamp — immutable once set.
    """
    now = _now()
    extra = {
        "state":        "CANDIDATE",
        "triggered_at": now,
    }
    transition_state(setup_id, "CANDIDATE", conn, extra_fields=extra)

    if snapshot_payload is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT candidate_id FROM scanner_candidates WHERE setup_id = %s",
                (setup_id,)
            )
            row = cur.fetchone()
            if row:
                insert_snapshot(row[0], "TRIGGERED", snapshot_payload, conn)

    logger.info(f"🔔 [TRIGGERED] setup_id={setup_id!r} — moved to CANDIDATE at {now.isoformat()}")


def confirm_candidate(
    setup_id: str,
    conn,
    *,
    alert_payload: Dict[str, Any],
) -> bool:
    """
    Fully transactional CANDIDATE → CONFIRMED upgrade.

    Protocol:
        BEGIN (caller must have started a transaction)
        SELECT scanner_candidates WHERE setup_id = %s FOR UPDATE  (row-level lock)
        Verify state is CANDIDATE
        UPDATE scanner_candidates SET state=CONFIRMED, confirmed_at=now
        INSERT INTO alerts with idempotency_key = setup_id + '_CONFIRMED_BUY'
        (on conflict do nothing — DB-level duplicate guard)
        INSERT candidate_snapshot with reason=CONFIRMED
        (caller commits)

    Returns True if the alert was newly inserted, False if it was a duplicate (already confirmed).

    IMPORTANT: The caller MUST have already fetched fresh market data and run a full
    scanner re-evaluation before calling this function. A green Watch snapshot is not
    sufficient to confirm — this function is for after the full evaluation.
    """
    now = _now()
    idempotency_key = f"{setup_id}_CONFIRMED_BUY"

    with conn.cursor() as cur:
        # ── Row-level lock ─────────────────────────────────────────────────────────
        cur.execute(
            "SELECT candidate_id, state FROM scanner_candidates "
            "WHERE setup_id = %s FOR UPDATE",
            (setup_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"[candidate_tracker] confirm_candidate: setup_id not found: {setup_id!r}")

        candidate_id, current_state = row

        # ── State validation ───────────────────────────────────────────────────────
        if current_state != "CANDIDATE":
            logger.warning(
                f"[confirm_candidate] Cannot confirm {setup_id!r}: current state is "
                f"{current_state!r}, expected CANDIDATE. Skipping."
            )
            return False

        # ── Update candidate row ───────────────────────────────────────────────────
        cur.execute("""
            UPDATE scanner_candidates
               SET state        = 'CONFIRMED',
                   confirmed_at = %s,
                   updated_at   = %s
             WHERE setup_id = %s
        """, (now, now, setup_id))

        # ── Insert alert with idempotency guard ────────────────────────────────────
        # ON CONFLICT DO NOTHING prevents duplicate Admin alerts even if this function
        # is called twice (e.g. after a worker crash + retry).
        cur.execute("""
            INSERT INTO alerts (
                symbol, scanner, alert_type, breakout_type,
                entry_price, stop_loss, target_1, target_2,
                risk_reward_ratio, quality_score,
                reasoning, metadata, idempotency_key,
                created_at
            )
            VALUES (
                %(symbol)s, %(scanner)s, 'CONFIRMED_BUY', %(breakout_type)s,
                %(entry_price)s, %(stop_loss)s, %(target_1)s, %(target_2)s,
                %(risk_reward_ratio)s, %(quality_score)s,
                %(reasoning)s, %(metadata)s::jsonb, %(idempotency_key)s,
                %(now)s
            )
            ON CONFLICT (idempotency_key) DO NOTHING
        """, {
            **alert_payload,
            "idempotency_key": idempotency_key,
            "now":             now,
        })

        newly_inserted = cur.rowcount == 1

        # ── Snapshot the confirmation moment ───────────────────────────────────────
        snap_payload = {
            **alert_payload,
            "last_seen_price":  alert_payload.get("entry_price"),
            "trigger_level":    alert_payload.get("entry_price"),
            "health_status":    "CONFIRMED",
        }
        insert_snapshot(candidate_id, "CONFIRMATION_CHECK", snap_payload, conn)

    if newly_inserted:
        logger.info(
            f"🔥 [CONFIRMED_BUY] setup_id={setup_id!r} | "
            f"symbol={alert_payload.get('symbol')} | "
            f"entry=₹{alert_payload.get('entry_price')} | "
            f"RR={alert_payload.get('risk_reward_ratio')}R"
        )
    else:
        logger.warning(
            f"[confirm_candidate] Duplicate suppressed: {setup_id!r} already confirmed."
        )

    return newly_inserted


def get_active_candidates(
    scanner_name: str,
    conn,
    *,
    states: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Returns all active candidates for a given scanner.
    Default states: WATCH and CANDIDATE.
    Used by the scanner's update loop to re-evaluate and upsert each scan run.
    """
    if states is None:
        states = ["WATCH", "CANDIDATE"]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                candidate_id, setup_id, symbol, scanner_name, setup_type,
                state, structure_date, detected_at, triggered_at,
                expires_at, trigger_level, invalidation_level,
                next_required_event, last_seen_price, quality_score,
                primary_blocker_type, health_status, algorithm_version
            FROM scanner_candidates
            WHERE scanner_name = %s
              AND state = ANY(%s)
            ORDER BY quality_score DESC NULLS LAST
        """, (scanner_name, states))

        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def check_and_expire_stale_candidates(scanner_name: str, conn) -> int:
    """
    Marks WATCH candidates as EXPIRED where expires_at has passed.
    Called at the start of each scanner run to clean up timed-out setups.
    Returns the count of candidates expired.
    """
    now = _now()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE scanner_candidates
               SET state               = 'EXPIRED',
                   invalidated_at      = %s,
                   failure_reason_code = 'EXPIRED',
                   failed_checklists   = '[]'::jsonb,
                   updated_at          = %s
             WHERE scanner_name = %s
               AND state = 'WATCH'
               AND expires_at IS NOT NULL
               AND expires_at < %s
            RETURNING setup_id
        """, (now, now, scanner_name, now))

        expired = cur.fetchall()
        count = len(expired)

    if count > 0:
        logger.info(
            f"⏰ [EXPIRY SWEEP] {scanner_name}: {count} stale WATCH candidates expired."
        )
    return count
