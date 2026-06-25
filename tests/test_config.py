import os
import importlib
import pytest
import app.config as config

def test_config_directories():
    # DATA_DIR should be an absolute path and must exist
    assert os.path.isabs(config.DATA_DIR)
    assert os.path.isdir(config.DATA_DIR)

def test_config_env_vars(monkeypatch):
    # Mock environment variables
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.setenv("CHAT_ID", "12345")
    monkeypatch.setenv("THREAD_EOD", "99")
    
    # Reload config to apply mocked env vars
    importlib.reload(config)
    
    assert config.BOT_TOKEN == "test_token"
    assert config.CHAT_ID == "12345"
    assert config.THREAD_EOD == 99
    
    # Verify defaults
    assert config.MIN_STOCK_PRICE == 100.0
    assert config.LOG_LEVEL == "INFO"
