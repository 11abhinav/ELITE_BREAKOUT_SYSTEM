import logging
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from session_context import SessionContext

logger = logging.getLogger("ApplicationContext")

# [VERSION: SESSION_ARCH_v2A_0] ApplicationContext wired into main.py startup.
# Previously this was a skeleton with no callers. As of Phase 2A it is
# instantiated at process boot and owns the SessionContext lifecycle.


class ApplicationContext:
    """
    Process-lifetime singleton. Owns long-lived services across multiple
    trading sessions.

    Clearly separates:
        - Application lifetime   (this object — lives for the duration of the process)
        - Session lifetime       (SessionContext — one per trading day, midnight rotation)

    Scanners should never hold a reference to ApplicationContext directly.
    They interact only with the SessionContext passed to them at scan time.
    """

    _instance: Optional["ApplicationContext"] = None

    @classmethod
    def get_instance(cls) -> "ApplicationContext":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.session_context = None

        # ── Long-lived services (process lifetime) ──────────────────────────
        # Imported lazily to avoid circular imports at module load time.
        self._db = None
        self._telemetry = None
        self._config = None

        # Log the object identity (memory address) so production logs can
        # confirm only one instance ever exists across the process lifetime.
        logger.info(
            f"✅ ApplicationContext initialised | "
            f"instance_id={id(self):#x} (process lifetime)."
        )


    # ── Service accessors ────────────────────────────────────────────────────

    @property
    def db(self):
        if self._db is None:
            from database import get_connection
            self._db = get_connection
        return self._db

    @property
    def telemetry(self):
        if self._telemetry is None:
            from telemetry_manager import telemetry as _t
            self._telemetry = _t
        return self._telemetry

    @property
    def config(self):
        if self._config is None:
            import config as _c
            self._config = _c
        return self._config

    # ── Session lifecycle ────────────────────────────────────────────────────

    def create_session(self) -> "SessionContext":
        """
        Create a new SessionContext for today's trading day.
        If a previous session exists it is destroyed first.
        """
        if self.session_context is not None:
            logger.warning(
                "ApplicationContext.create_session() called while a session "
                "already exists. Destroying old session first."
            )
            self.destroy_session()

        from session_context import SessionContext
        self.session_context = SessionContext()
        logger.info(
            f"✅ [SESSION] New SessionContext created | "
            f"instance_id={id(self):#x} | "
            f"State: {self.session_context.state.name}"
        )
        try:
            self.telemetry.log_session_timeline("SessionContext created (new trading day)")
        except Exception:
            pass
        return self.session_context

    def destroy_session(self):
        """
        Destroy the current SessionContext and release all its managed memory.
        Called at midnight to ensure a clean start the next trading day.
        """
        if self.session_context is None:
            return
        try:
            self.session_context.destroy()
        except Exception as e:
            logger.warning(f"Error during SessionContext destroy: {e}")
        finally:
            self.session_context = None

        # ── MIDNIGHT COMPREHENSIVE MEMORY & CACHE ROTATION (5GB RAM OPTIMIZED) ────
        try:
            # 1. Clear PriceProvider in-memory cache
            from data_provider import _price_provider
            if hasattr(_price_provider, "cache"):
                with getattr(_price_provider, "cache_lock", threading.Lock()):
                    _price_provider.cache.clear()
                logger.info("🧹 [MIDNIGHT ROTATION] Cleared PriceProvider in-memory cache.")
        except Exception as e:
            logger.warning(f"Error clearing PriceProvider cache at midnight: {e}")

        try:
            # 2. Clear Fyers degradation cache
            from data_provider import _fyers_degradation_cache
            _fyers_degradation_cache.clear()
            logger.info("🧹 [MIDNIGHT ROTATION] Cleared Fyers degradation cache.")
        except Exception as e:
            logger.warning(f"Error clearing Fyers degradation cache at midnight: {e}")

        try:
            # 3. Trigger full garbage collection & native memory arena malloc_trim
            import gc, ctypes
            gc.collect()
            try:
                ctypes.CDLL('libc.so.6').malloc_trim(0)
                logger.info("🧹 [MIDNIGHT ROTATION] Reclaimed native memory arena via malloc_trim(0).")
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Error during garbage collection at midnight: {e}")

        logger.info(f"🗑️ [SESSION] SessionContext destroyed | instance_id={id(self):#x} (midnight rotation).")
        try:
            self.telemetry.log_session_timeline("SessionContext destroyed (midnight rotation)")
        except Exception:
            pass

    def new_trading_day(self) -> "SessionContext":
        """
        Convenience wrapper: destroy old session and create a fresh one.
        Called by the midnight scheduler tick.
        """
        self.destroy_session()
        return self.create_session()
