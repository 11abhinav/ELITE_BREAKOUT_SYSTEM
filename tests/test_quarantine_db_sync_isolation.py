"""
P0 regression: test/temporary quarantine stores must NEVER touch production PostgreSQL.

    temporary store
        -> local parquet writes allowed
        -> PostgreSQL writes (upload) = 0
        -> PostgreSQL reads (download seed) = 0
"""
import os
import tempfile
from unittest import mock

from app import pit_recovery_cache as prc
from app.pit_recovery_cache import (
    PitRecoveryStatusStore,
    is_quarantine_db_sync_allowed,
    PRODUCTION_QUARANTINE_PATH,
    QUARANTINE_DB_SYNC_ENV,
)


def _record(store, sym="ISO_SYM"):
    return store.record_unavailability(
        symbol=sym,
        provider="NSE_XBRL",
        status="CONFIRMED_NO_DATA_ANYWHERE",
        reason="CONFIRMED_NO_DATA_ANYWHERE",
        scanner_family="QUALITY_COMPOUNDER",
        field_name="sales_cagr_5y",
        raw_record_count=0,
    )


def test_temp_store_local_writes_allowed_zero_postgres_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "pit_recovery_status.parquet")
        with mock.patch("app.database.upload_parquet_to_db") as up, \
             mock.patch("app.database.submit_background_upload") as sub, \
             mock.patch("app.database.download_parquet_from_db") as down:
            store = PitRecoveryStatusStore(parquet_path=path)
            assert store.db_sync_enabled is False
            _record(store)

            # Local write happened
            assert os.path.exists(path)
            assert store.is_quarantined_for_scanner("ISO_SYM", "QUALITY_COMPOUNDER") is True

            # Zero PostgreSQL interaction
            assert store.db_sync_attempts == 0
            up.assert_not_called()
            sub.assert_not_called()
            down.assert_not_called()


def test_non_production_path_cannot_enable_sync_even_with_env_true():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "pit_recovery_status.parquet")
        with mock.patch.dict(os.environ, {QUARANTINE_DB_SYNC_ENV: "true"}):
            assert is_quarantine_db_sync_allowed(path) is False


def test_production_path_respects_env_kill_switch():
    with mock.patch.dict(os.environ, {QUARANTINE_DB_SYNC_ENV: "false"}):
        assert is_quarantine_db_sync_allowed(PRODUCTION_QUARANTINE_PATH) is False
    with mock.patch.dict(os.environ, {QUARANTINE_DB_SYNC_ENV: "true"}):
        assert is_quarantine_db_sync_allowed(PRODUCTION_QUARANTINE_PATH) is True


def test_pytest_session_has_sync_disabled():
    """conftest.py must disable quarantine DB sync for the whole session."""
    assert os.environ.get(QUARANTINE_DB_SYNC_ENV) == "false"
    assert is_quarantine_db_sync_allowed(prc.DEFAULT_RECOVERY_STATUS_PATH) is False
