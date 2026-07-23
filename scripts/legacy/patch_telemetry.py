with open("app/telemetry_manager.py", "r") as f:
    content = f.read()

# Add log_scheduler_event method
method_str = """
    def log_scheduler_event(self, name: str, event_type: str, error: str = None):
        if not self.enabled: return
        msg = f"[{event_type}] Scheduler: {name}"
        if error:
            msg += f" (Error: {error})"
        logger.debug(msg)
"""

# Insert it before log_scheduler
content = content.replace("    def log_scheduler(", method_str + "\n    def log_scheduler(")

with open("app/telemetry_manager.py", "w") as f:
    f.write(content)
