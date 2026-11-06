import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MQTT_ENABLED", "false")
    monkeypatch.setenv("AGENT_PROVIDER", "rules")
    monkeypatch.setenv("DIAGNOSTIC_DB", str(tmp_path / "runs.sqlite3"))
