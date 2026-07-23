import logging
from typing import Optional

logger = logging.getLogger("ApplicationContext")

class ApplicationContext:
    """
    Bootstrap object that owns long-lived services across multiple sessions.
    Clearly separates application lifetime from trading session lifetime.
    """
    _instance = None  # ApplicationContext can be a singleton, SessionContext is not.

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.session_context = None
        self.logger = logger
        # In a full DI setup, these would be initialized here:
        # self.database_manager = DatabaseManager()
        # self.scheduler = Scheduler()
        # self.configuration = Configuration()
        # self.telemetry = TelemetryManager()
    
    def create_session(self):
        """Creates a new SessionContext for a trading day."""
        from session_context import SessionContext
        if self.session_context is not None:
            self.destroy_session()
            
        self.session_context = SessionContext()
        return self.session_context
        
    def destroy_session(self):
        """Destroys the current trading session and frees its memory."""
        if self.session_context:
            self.session_context.transition_to("SHUTTING_DOWN")
            self.session_context.transition_to("DESTROYED")
            self.session_context = None
