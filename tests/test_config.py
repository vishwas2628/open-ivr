"""Layered configuration loader."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from openivr.builder.server import read_smtp, write_smtp
from openivr.config import ConfigError, SmtpCfg, load_config


def test_defaults_without_files(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.app.stasis_app == "openivr"
    assert config.dial.mode == "dialplan"
    assert config.dial.context == "openivr-dial"
    assert config.cdr.backend == "csv"
    assert config.ari.base_url.startswith("http://")
    assert config.ivr.interdigit_timeout > 0
    assert config.ivr.max_menu_depth >= 1
    assert config.voicemail.enabled is True
    assert config.sources == ["defaults"]


def test_yaml_layer(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "app:\n  stasis_app: demo\ndial:\n  mode: originate\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.app.stasis_app == "demo"
    assert config.dial.mode == "originate"
    assert config.sources == ["config.yaml"]


def test_system_json_overrides_yaml(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("ari:\n  password: from-yaml\n", encoding="utf-8")
    (tmp_path / "system.json").write_text(
        json.dumps({"ari": {"password": "from-system", "base_url": "http://10.0.0.5:8088"}}),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.ari.password == "from-system"
    assert config.ari.base_url == "http://10.0.0.5:8088"
    assert config.sources == ["config.yaml", "system.json"]


def test_smtp_json_layer(tmp_path: Path) -> None:
    write_smtp(
        tmp_path,
        {"enabled": True, "host": "smtp.example.com", "port": 2525, "alerts_to": ["a@b.c"]},
    )
    config = load_config(tmp_path)
    assert config.smtp.enabled is True
    assert config.smtp.host == "smtp.example.com"
    assert config.smtp.port == 2525
    assert config.smtp.alerts_to == ["a@b.c"]
    assert "data/smtp.json" in config.sources


def test_env_layer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENIVR_ARI_PASSWORD", "env-pass")
    monkeypatch.setenv("OPENIVR_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("OPENIVR_SMTP_ALERTS_TO", '["x@y.z","w@y.z"]')
    monkeypatch.setenv("OPENIVR_ARI_RECONNECT_MAX", "90")
    monkeypatch.setenv("OPENIVR_VOICEMAIL_ENABLED", "no")
    config = load_config(tmp_path)
    assert config.ari.password == "env-pass"
    assert config.logging.level == "DEBUG"
    assert config.smtp.alerts_to == ["x@y.z", "w@y.z"]
    assert config.voicemail.enabled is False
    assert config.ari.reconnect_max == 90.0


def test_env_alias_for_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENIVR_DB_PASSWORD", "s3cret")
    config = load_config(tmp_path)
    assert config.cdr.postgres.password == "s3cret"


def test_paths_are_absolute(project: Path) -> None:
    config = load_config(project)
    assert config.root == project
    assert config.flow_path.is_absolute()
    assert config.sounds_dir == project / "data" / "sounds"
    assert config.recordings_dir == project / "data" / "recordings"
    assert config.voicemail_dir == project / "data" / "recordings" / "voicemail"
    assert config.logs_dir == project / "data" / "logs"
    assert config.path("data/x.json") == project / "data" / "x.json"
    assert config.path("/tmp/x") == Path("/tmp/x")


def test_ensure_dirs(project: Path) -> None:
    config = load_config(project)
    config.ensure_dirs()
    for path in (config.voicemail_dir, config.logs_dir, config.path("data/flows")):
        assert path.is_dir()


def test_require_ari_password(tmp_path: Path) -> None:
    from openivr.config import ConfigError

    config = load_config(tmp_path)
    with pytest.raises(ConfigError):
        config.require_ari_password()
    config.ari.password = "x"
    assert config.require_ari_password() == "x"


def test_invalid_values_are_rejected(tmp_path: Path) -> None:
    from openivr.config import ConfigError

    (tmp_path / "config.yaml").write_text("dial:\n  mode: carrier-pigeon\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)

    (tmp_path / "config.yaml").write_text("cdr:\n  backend: mysql\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)

    (tmp_path / "config.yaml").write_text("smtp:\n  enabled: true\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_smtp_roundtrip(tmp_path: Path) -> None:
    assert read_smtp(tmp_path) == {}
    cfg = SmtpCfg(enabled=True, host="smtp.x", alerts_to=["ops@x"])
    write_smtp(tmp_path, asdict(cfg))
    data = json.loads((tmp_path / "data" / "smtp.json").read_text(encoding="utf-8"))
    assert data["smtp"]["host"] == "smtp.x"
    assert read_smtp(tmp_path)["host"] == "smtp.x"
    assert read_smtp(tmp_path)["alerts_to"] == ["ops@x"]


def test_broken_json_raises(tmp_path: Path) -> None:
    from openivr.config import ConfigError

    (tmp_path / "system.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_unreadable_system_json_explains_the_fix(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The installer runs under sudo; the CLI/builder run as the user."""
    from pathlib import Path as _Path

    target = tmp_path / "system.json"
    target.write_text("{}", encoding="utf-8")
    real_open = _Path.open

    def deny(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "open", deny)
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)
    message = str(excinfo.value)
    assert "permission denied" in message
    assert "sudo chown" in message
