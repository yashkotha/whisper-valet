# Changelog

## 0.1.0 - 2026-07-22

Initial release.

- Drop-folder pipeline: launchd daemon watches `~/AudioDrop`, processes each
  clip exactly once (move-out semantics), outputs a self-contained folder per
  clip: cleaned audio (Demucs voice isolation + leveling), speaker-labeled
  transcript (.txt/.srt/.json with word timestamps), confidence report.
- Any speaker count: `auto` detection per clip by default, exact-N forcing, or
  MIN-MAX bounds. Label names are a hint, applied only on an exact count match.
- Diarization: pyannote 3.1 runs on the original-timbre audio and is merged
  onto MacWhisper's word-level transcript; graceful fallback to MacWhisper's
  built-in speaker detection when pyannote is unavailable.
- MCP server (`whisper-valet-mcp`): process_audio, process_folder, status,
  list_clips, get_transcript, get_report, configure, transcribe_quick,
  list_models. Queue-based design — no long-blocking MCP calls.
- Hardening: path allow-list with symlink resolution, null-byte checks,
  extension validation, model-id validation, output size caps, argv-only
  subprocess calls.
- One-command installer (`setup.sh`) with pinned Python environments.
