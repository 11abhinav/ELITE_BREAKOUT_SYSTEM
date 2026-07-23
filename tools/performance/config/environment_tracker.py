import os
import platform
import subprocess
import hashlib
import json

def get_git_revision_hash() -> str:
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip()
    except Exception:
        return "UNKNOWN"

def get_git_branch() -> str:
    try:
        return subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode('ascii').strip()
    except Exception:
        return "UNKNOWN"

def is_git_dirty() -> bool:
    try:
        return bool(subprocess.check_output(['git', 'status', '--porcelain']))
    except Exception:
        return True

def get_config_hash() -> str:
    """Creates a hash of the current config.py to detect silent changes to thresholds."""
    try:
        with open("app/config.py", "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return "UNKNOWN"

def capture_environment_state() -> dict:
    """Captures the deterministic environment variables and drift markers."""
    return {
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "git_commit": get_git_revision_hash(),
        "git_branch": get_git_branch(),
        "is_dirty": is_git_dirty(),
        "config_hash": get_config_hash(),
        "budget_version": "v1"
    }

def dump_environment_state(filepath: str):
    with open(filepath, 'w') as f:
        json.dump(capture_environment_state(), f, indent=4)
