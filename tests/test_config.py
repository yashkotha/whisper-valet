"""Config parsing + path allow-list security tests."""
import os
from pathlib import Path

import pytest

from whisper_valet.config import Config, parse_config_env, validate_media_path


def make_config(tmp_path: Path, allowed: list[Path]) -> Config:
    return Config(
        pipe_dir=tmp_path, inbox=tmp_path / "inbox", outbox=tmp_path / "outbox",
        mw_cli="/nonexistent/mw", allowed_paths=tuple(p.resolve() for p in allowed),
    )


def test_parse_config_env(tmp_path):
    f = tmp_path / "config.env"
    f.write_text(
        '# comment\n'
        'NUM_SPEAKERS=2\n'
        'LABELS="Interviewer,Responder"\n'
        'INBOX="/tmp/somewhere"  # trailing comment\n'
        'not a config line\n'
    )
    cfg = parse_config_env(f)
    assert cfg["NUM_SPEAKERS"] == "2"
    assert cfg["LABELS"] == "Interviewer,Responder"
    assert cfg["INBOX"] == "/tmp/somewhere"
    assert "not" not in cfg


def test_parse_config_env_missing_file(tmp_path):
    assert parse_config_env(tmp_path / "nope.env") == {}


def test_env_var_overrides_config_file(tmp_path, monkeypatch):
    (tmp_path / "config.env").write_text('INBOX="/from/file"\n')
    monkeypatch.setenv("VALET_INBOX", str(tmp_path / "from-env"))
    cfg = Config.load(pipe_dir=tmp_path)
    assert cfg.inbox == tmp_path / "from-env"


def test_allowed_path_accepts_inside(tmp_path):
    cfg = make_config(tmp_path, [tmp_path])
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    assert cfg.is_path_allowed(f)


def test_allowed_path_rejects_outside(tmp_path):
    inside = tmp_path / "allowed"
    inside.mkdir()
    outside = tmp_path / "outside" / "a.mp3"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    cfg = make_config(tmp_path, [inside])
    assert not cfg.is_path_allowed(outside)


def test_allowed_path_rejects_escaping_symlink(tmp_path):
    inside = tmp_path / "allowed"
    inside.mkdir()
    secret = tmp_path / "secret.mp3"
    secret.write_bytes(b"x")
    link = inside / "link.mp3"
    link.symlink_to(secret)
    cfg = make_config(tmp_path, [inside])
    # symlink resolves outside the allow-list -> rejected
    assert not cfg.is_path_allowed(link)


def test_validate_media_path_null_byte(tmp_path):
    cfg = make_config(tmp_path, [tmp_path])
    with pytest.raises(ValueError, match="null byte"):
        validate_media_path("a\x00b.mp3", cfg)


def test_validate_media_path_missing(tmp_path):
    cfg = make_config(tmp_path, [tmp_path])
    with pytest.raises(ValueError, match="not found"):
        validate_media_path(str(tmp_path / "missing.mp3"), cfg)


def test_validate_media_path_bad_extension(tmp_path):
    cfg = make_config(tmp_path, [tmp_path])
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    with pytest.raises(ValueError, match="Unsupported file type"):
        validate_media_path(str(f), cfg)


def test_validate_media_path_denied(tmp_path):
    inside = tmp_path / "allowed"
    inside.mkdir()
    cfg = make_config(tmp_path, [inside])
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="Access denied"):
        validate_media_path(str(f), cfg)


def test_defaults_when_no_config(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("VALET_"):
            monkeypatch.delenv(k)
    cfg = Config.load(pipe_dir=tmp_path)
    assert cfg.inbox == Path.home() / "AudioDrop"
    assert cfg.outbox == Path.home() / "AudioDrop" / "Processed"
    assert Path.home().resolve() in cfg.allowed_paths
