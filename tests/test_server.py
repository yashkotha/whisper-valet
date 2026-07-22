"""MCP server tool tests (subprocess + daemon interactions mocked)."""
import asyncio
from unittest.mock import patch

import pytest

from whisper_valet.config import Config
from whisper_valet.server import build_server


@pytest.fixture()
def cfg(tmp_path):
    inbox = tmp_path / "AudioDrop"
    return Config(
        pipe_dir=tmp_path, inbox=inbox, outbox=inbox / "Processed",
        mw_cli="/nonexistent/mw", allowed_paths=(tmp_path.resolve(),),
    )


@pytest.fixture()
def server(cfg):
    with patch("whisper_valet.server.subprocess.run") as _run, \
         patch("whisper_valet.server.subprocess.Popen") as _popen:
        _run.return_value.returncode = 0
        srv = build_server(cfg)
        yield srv


def call(server, tool, **kwargs):
    result = asyncio.run(server.call_tool(tool, kwargs))
    # FastMCP returns (content_blocks, raw) — take the text of the first block
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


def test_process_audio_queues_file(server, cfg, tmp_path):
    f = tmp_path / "talk.mp3"
    f.write_bytes(b"fake audio")
    out = call(server, "process_audio", path=str(f))
    assert "Queued" in out
    assert (cfg.inbox / "talk.mp3").exists()
    # original untouched (copied, not moved)
    assert f.exists()


def test_process_audio_rejects_missing(server):
    out = call(server, "process_audio", path="/nope/missing.mp3")
    assert out.startswith("ERROR")


def test_process_audio_rejects_bad_extension(server, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    out = call(server, "process_audio", path=str(f))
    assert "Unsupported file type" in out


def test_process_audio_rejects_outside_allowlist(server, tmp_path):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        out = call(server, "process_audio", path=f.name)
    assert "Access denied" in out or "ERROR" in out


def test_process_folder_queues_all_media(server, cfg, tmp_path):
    d = tmp_path / "batch"
    d.mkdir()
    (d / "a.mp3").write_bytes(b"x")
    (d / "b.wav").write_bytes(b"x")
    (d / "notes.txt").write_text("skip me")
    out = call(server, "process_folder", path=str(d))
    assert "Queued 2 file(s)" in out
    assert (cfg.inbox / "a.mp3").exists()
    assert not (cfg.inbox / "notes.txt").exists()


def test_status_empty(server):
    out = call(server, "status")
    assert "inbox waiting: none" in out
    assert "watcher busy: no" in out


def test_status_reports_clip_states(server, cfg):
    clip = cfg.outbox / "Interview1"
    clip.mkdir(parents=True)
    (clip / "status.txt").write_text("done")
    out = call(server, "status")
    assert "Interview1: done" in out


def test_get_transcript_not_ready(server, cfg):
    clip = cfg.outbox / "Pending"
    clip.mkdir(parents=True)
    (clip / "status.txt").write_text("processing")
    out = call(server, "get_transcript", clip="Pending")
    assert "not ready" in out
    assert "processing" in out


def test_get_transcript_appends_verify_flags(server, cfg):
    clip = cfg.outbox / "Done1"
    clip.mkdir(parents=True)
    (clip / "transcript.txt").write_text("[0:00] A:\n  Hello\n")
    (clip / "report.txt").write_text("[ 3] 1:07 B 0.40 Hmm  <-- verify\n")
    out = call(server, "get_transcript", clip="Done1")
    assert "Hello" in out
    assert "low-confidence" in out


def test_configure_updates_config_env(server, cfg):
    out = call(server, "configure", num_speakers="3", labels="Host,Guest 1,Guest 2")
    assert "NUM_SPEAKERS=3" in out
    text = (cfg.pipe_dir / "config.env").read_text()
    assert "NUM_SPEAKERS=3" in text
    assert 'LABELS="Host,Guest 1,Guest 2"' in text


def test_configure_accepts_auto_and_range(server, cfg):
    out = call(server, "configure", num_speakers="auto")
    assert "NUM_SPEAKERS=auto" in out
    out = call(server, "configure", num_speakers="2-5")
    assert "NUM_SPEAKERS=2-5" in out


def test_configure_rejects_bad_spec(server):
    out = call(server, "configure", num_speakers="lots")
    assert out.startswith("ERROR")


def test_configure_noop_leaves_file_absent(server, cfg):
    out = call(server, "configure")
    assert "Active config" in out
    assert not (cfg.pipe_dir / "config.env").exists()


def test_transcribe_quick_rejects_bad_model(server, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    out = call(server, "transcribe_quick", path=str(f), model="bad model; rm -rf")
    assert "invalid model identifier" in out
