"""Tests for AWS env-var startup validation in server_agent."""
import pytest
import server_agent


REQUIRED_VARS = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]


def _run_validate(monkeypatch, env: dict):
    """
    Call server_agent.validate_aws_env() with a controlled environment.
    Clears all three required vars first, then sets only what is in `env`.
    Does NOT reload the module — just calls the function directly.
    """
    for var in REQUIRED_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    server_agent.validate_aws_env()


def test_all_vars_present_does_not_exit(monkeypatch):
    """No exception when all three vars are set."""
    _run_validate(monkeypatch, {
        "AWS_ACCESS_KEY_ID": "AKIATEST",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_DEFAULT_REGION": "us-east-1",
    })
    # reaching here means no SystemExit was raised


def test_missing_access_key_exits(monkeypatch):
    """sys.exit(1) when AWS_ACCESS_KEY_ID is absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_DEFAULT_REGION": "us-east-1",
        })
    assert exc_info.value.code == 1


def test_missing_secret_key_exits(monkeypatch):
    """sys.exit(1) when AWS_SECRET_ACCESS_KEY is absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_DEFAULT_REGION": "us-east-1",
        })
    assert exc_info.value.code == 1


def test_missing_region_exits(monkeypatch):
    """sys.exit(1) when AWS_DEFAULT_REGION is absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_SECRET_ACCESS_KEY": "secret",
        })
    assert exc_info.value.code == 1


def test_all_vars_missing_exits(monkeypatch):
    """sys.exit(1) when all vars are absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {})
    assert exc_info.value.code == 1
