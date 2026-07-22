"""AudioDrop MCP server (stdio).

Two kinds of tools:

* **Queue tools** (`process_audio`, `process_folder`) — instant: they validate,
  copy the file into the AudioDrop inbox and wake the launchd watcher daemon.
  The heavy pipeline (Demucs voice isolation -> MacWhisper CLI transcription ->
  pyannote diarization) runs in the daemon, so no MCP call ever blocks for
  minutes or trips a client timeout. Results are read back with `status` /
  `get_transcript` / `get_report`.

* **Direct tools** (`transcribe_quick`, `list_models`) — synchronous wrappers
  around the MacWhisper CLI for short clips where you just want text now,
  without enhancement or diarization.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import MAX_OUTPUT_BYTES, MEDIA_EXTENSIONS, MODEL_RE, Config, validate_media_path

# stdio transport: stdout is the JSON-RPC stream — log to stderr only.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("whisper-valet")

LAUNCHD_LABEL = "com.whispervalet.watcher"
QUICK_TIMEOUT_S = 600  # transcribe_quick hard cap (10 min)


def build_server(config: Config | None = None) -> FastMCP:
    cfg = config or Config.load()
    mcp = FastMCP("whisper-valet")

    # ---------- helpers ----------

    def _kick_watcher() -> None:
        """Ensure the watcher runs soon: kickstart the launchd job, else spawn it."""
        r = subprocess.run(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            capture_output=True,
        )
        if r.returncode != 0:  # agent not loaded — run the watcher directly
            subprocess.Popen(
                ["/bin/bash", str(cfg.pipe_dir / "watcher.sh")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    def _clip_state(clipdir: Path) -> str:
        st = clipdir / "status.txt"
        return st.read_text().strip() if st.exists() else "unknown"

    def _enqueue(resolved: Path) -> None:
        cfg.inbox.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved, cfg.inbox / resolved.name)  # copy, never move

    # ---------- queue tools ----------

    @mcp.tool()
    def process_audio(path: str) -> str:
        """Queue one audio/video file for the full pipeline (voice isolation ->
        transcription -> speaker diarization). Returns immediately; results land
        in <outbox>/<clip>/ minutes later — poll status()."""
        try:
            resolved = validate_media_path(path, cfg)
        except ValueError as e:
            return f"ERROR: {e}"
        if cfg.outbox in resolved.parents or resolved.parent == cfg.inbox:
            return f"ERROR: {resolved} is already inside the pipeline folders"
        _enqueue(resolved)
        _kick_watcher()
        return (f"Queued '{resolved.name}'. Results will appear in "
                f"{cfg.outbox}/{resolved.stem}/ (cleaned audio + transcript.txt/.srt/.json "
                f"+ report.txt). A 3-minute clip takes ~5 minutes.")

    @mcp.tool()
    def process_folder(path: str) -> str:
        """Queue every audio/video file in a folder (non-recursive)."""
        src = Path(path).expanduser()
        if not src.is_dir():
            return f"ERROR: not a folder: {src}"
        if not cfg.is_path_allowed(src):
            return f"ERROR: folder outside allowed paths ({', '.join(map(str, cfg.allowed_paths))})"
        queued = []
        for f in sorted(src.iterdir()):
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                _enqueue(f.resolve())
                queued.append(f.name)
        if not queued:
            return f"No audio/video files found in {src}"
        _kick_watcher()
        return (f"Queued {len(queued)} file(s): {', '.join(queued)}. "
                f"Results in {cfg.outbox}/<clip>/ per file.")

    # ---------- result tools ----------

    @mcp.tool()
    def status() -> str:
        """Pipeline status: inbox queue, watcher activity, recent clips, log tail."""
        waiting = [f.name for f in cfg.inbox.glob("*")
                   if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS] \
            if cfg.inbox.exists() else []
        rows: list[str] = []
        if cfg.outbox.exists():
            clips = sorted((c for c in cfg.outbox.iterdir() if c.is_dir()),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            rows = [f"  {c.name}: {_clip_state(c)}" for c in clips[:15]]
        busy = (cfg.pipe_dir / "logs" / ".watcher.lock").exists()
        out = [f"inbox waiting: {waiting or 'none'}",
               f"watcher busy: {'yes' if busy else 'no'}",
               "recent clips:"]
        out += rows or ["  (none yet)"]
        logf = cfg.pipe_dir / "logs" / "watcher.log"
        if logf.exists():
            out += ["last log lines:"] + [f"  {ln}" for ln in logf.read_text().splitlines()[-5:]]
        return "\n".join(out)

    @mcp.tool()
    def list_clips() -> str:
        """List all processed clip folders with their status."""
        if not cfg.outbox.exists():
            return "No clips processed yet."
        rows = [f"{c.name}: {_clip_state(c)}"
                for c in sorted(cfg.outbox.iterdir()) if c.is_dir()]
        return "\n".join(rows) or "No clips processed yet."

    @mcp.tool()
    def get_transcript(clip: str) -> str:
        """Speaker-labeled transcript for a processed clip (see list_clips())."""
        t = cfg.outbox / clip / "transcript.txt"
        if not t.exists():
            exists = (cfg.outbox / clip).exists()
            state = _clip_state(cfg.outbox / clip) if exists else "no such clip"
            return f"Transcript not ready for '{clip}' (state: {state})."
        body = t.read_text()
        rep = cfg.outbox / clip / "report.txt"
        if rep.exists():
            flagged = [ln for ln in rep.read_text().splitlines() if "verify" in ln]
            if flagged:
                body += ("\n\n-- low-confidence segments (worth a listen) --\n"
                         + "\n".join(flagged))
        return body

    @mcp.tool()
    def get_report(clip: str) -> str:
        """Full per-segment speaker-confidence report for a processed clip."""
        rep = cfg.outbox / clip / "report.txt"
        if not rep.exists():
            return f"No report for '{clip}' (state: {_clip_state(cfg.outbox / clip)})."
        return rep.read_text()

    # ---------- configuration ----------

    @mcp.tool()
    def configure(num_speakers: str = "", labels: str = "") -> str:
        """Adjust diarization for FUTURE clips. num_speakers: "auto" (detect
        per clip — works for any recording), "N" to force exactly N, or
        "MIN-MAX" to bound the range (e.g. "2-5"). labels: comma-separated
        names in order of first appearance, e.g. "Interviewer,Responder" —
        applied only when the detected speaker count matches the label count,
        otherwise speakers get generic "Speaker N" names. Pass "" to leave a
        setting unchanged. Returns the active configuration."""
        import re as _re
        if num_speakers and not _re.fullmatch(r"auto|\d+|\d+-\d+", num_speakers.strip().lower()):
            return f"ERROR: invalid num_speakers {num_speakers!r} — use auto, N, or MIN-MAX"
        f = cfg.pipe_dir / "config.env"
        lines = f.read_text().splitlines() if f.exists() else []

        def upsert(key: str, val: str) -> None:
            pat = _re.compile(rf"^\s*{key}\s*=")
            for i, ln in enumerate(lines):
                if pat.match(ln):
                    lines[i] = f"{key}={val}"
                    return
            lines.append(f"{key}={val}")

        if num_speakers:
            upsert("NUM_SPEAKERS", num_speakers.strip().lower())
        if labels:
            upsert("LABELS", f'"{labels}"')
        if num_speakers or labels:
            f.write_text("\n".join(lines) + "\n")
        from .config import parse_config_env
        live = parse_config_env(f)
        return (f"Active config: NUM_SPEAKERS={live.get('NUM_SPEAKERS', 'auto')} "
                f"LABELS={live.get('LABELS', '(generic Speaker N)')} "
                f"(applies to clips processed from now on)")

    # ---------- direct MacWhisper CLI tools ----------

    @mcp.tool()
    def transcribe_quick(path: str, model: str = "") -> str:
        """Plain synchronous transcription of a short clip via the MacWhisper
        CLI — no enhancement, no diarization, just text now. For noisy audio or
        speaker labels use process_audio() instead. Optional model override in
        engine:model-id format."""
        try:
            resolved = validate_media_path(path, cfg)
        except ValueError as e:
            return f"ERROR: {e}"
        if model and not MODEL_RE.fullmatch(model):
            return "ERROR: invalid model identifier (use engine:model-id format)"
        cmd = [cfg.mw_cli, "transcribe", str(resolved)]
        if model:
            cmd += ["--model", model]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=QUICK_TIMEOUT_S)
        except FileNotFoundError:
            return (f"ERROR: MacWhisper CLI not found at {cfg.mw_cli}. "
                    "Install MacWhisper, or set MW= in config.env.")
        except subprocess.TimeoutExpired:
            return (f"ERROR: transcription exceeded {QUICK_TIMEOUT_S}s — "
                    "use process_audio() for long recordings.")
        if proc.returncode != 0:
            return f"ERROR: MacWhisper CLI failed: {(proc.stderr or '').strip()[:500]}"
        out = proc.stdout
        if len(out.encode()) > MAX_OUTPUT_BYTES:
            return "ERROR: output exceeded size limit."
        return out.strip() or "(empty transcript)"

    @mcp.tool()
    def list_models() -> str:
        """List MacWhisper's downloaded transcription models (active one marked)."""
        try:
            proc = subprocess.run([cfg.mw_cli, "models", "list"],
                                  capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            return f"ERROR: MacWhisper CLI not found at {cfg.mw_cli}."
        except subprocess.TimeoutExpired:
            return "ERROR: 'mw models list' timed out."
        if proc.returncode != 0:
            return f"ERROR: {(proc.stderr or '').strip()[:300]}"
        return proc.stdout.strip()

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
