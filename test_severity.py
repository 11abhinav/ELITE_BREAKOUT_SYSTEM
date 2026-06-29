from app.database import classify_error_severity

error_msg = "Manual trigger failed: unsupported operand type(s) for -: 'datetime.datetime' and 'datetime.time'"
print("Severity:", classify_error_severity(error_msg))
