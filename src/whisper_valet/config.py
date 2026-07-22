"""Configuration for the AudioDrop MCP server.

Resolution order (highest wins):
  1. ``VALET_*`` environment variables
  2. ``config.env`` at the pipeline root (KEY=VALUE, shell-style)
  3. defaults under ``$HOME``
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# File types the pipeline accepts (video containers included — audio is extracted).
MEDIA_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
     ".aiff", ".aif", ".mp4", ".mov", ".m4v", ".webm"}
)

# Allowed characters in a MacWhisper model identifier (engine:model-id).
MODEL_RE = re.compile(r"[a-zA-Z0-9_:.-]+")

# Hard cap on quick-transcribe output, to guard against runaway CLI output.
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB

_CONFIG_LINE = re.compile(r'^\s*([A-Z_]+)\s*=\s*"?([^"#]*)"?\s*(?:#.*)?$')


def parse_config_env(path: Path) -> dict[str, str]:
    """Parse a shell-style KEY=VALUE config file (comments/blank lines ignored)."""
    cfg: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = _CONFIG_LINE.match(line)
            if m:
                cfg[m.group(1)] = m.group(2).strip()
    return cfg


@dataclass(frozen=True)
class Config:
    pipe_dir: Path
    inbox: Path
    outbox: Path
    mw_cli: str
    allowed_paths: tuple[Path, ...] = field(default_factory=tuple)

    @classmethod
    def load(cls, pipe_dir: Path | None = None) -> Config:
        pipe = (pipe_dir or Path(__file__).resolve().parents[2]).resolve()
        cfg = parse_config_env(pipe / "config.env")

        def get(key: str, default: str) -> str:
            return os.environ.get(f"VALET_{key}", cfg.get(key, default))

        inbox = Path(get("INBOX", str(Path.home() / "AudioDrop"))).expanduser()
        outbox = Path(get("OUTBOX", str(inbox / "Processed"))).expanduser()
        mw = get("MW", "/Applications/MacWhisper.app/Contents/MacOS/mw")

        raw = get("ALLOWED_PATHS", str(Path.home()))
        allowed = tuple(
            Path(p).expanduser().resolve()
            for p in raw.split(":") if p.strip()
        )
        return cls(pipe_dir=pipe, inbox=inbox, outbox=outbox,
                   mw_cli=mw, allowed_paths=allowed)

    def is_path_allowed(self, path: Path) -> bool:
        """True if ``path`` resolves inside an allow-listed directory.

        Symlinks are resolved *before* the prefix check, so a link inside an
        allowed folder pointing outside it is still rejected.
        """
        try:
            resolved = path.expanduser().resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            return False
        return any(
            resolved == base or base in resolved.parents
            for base in self.allowed_paths
        )


def validate_media_path(path_str: str, config: Config) -> Path:
    """Validate a user-supplied path; return the resolved Path or raise ValueError."""
    if "\x00" in path_str:
        raise ValueError("Invalid path: contains null byte.")
    p = Path(path_str).expanduser()
    try:
        resolved = p.resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise ValueError(f"File not found: {p}") from None
    if not resolved.is_file():
        raise ValueError(f"Not a file: {resolved}")
    if resolved.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{resolved.suffix}'. "
            f"Supported: {', '.join(sorted(MEDIA_EXTENSIONS))}"
        )
    if not config.is_path_allowed(resolved):
        allowed = ", ".join(str(p) for p in config.allowed_paths)
        raise ValueError(
            f"Access denied: '{resolved}' is outside the allowed folders ({allowed}). "
            "Move the file there, or extend VALET_ALLOWED_PATHS."
        )
    return resolved
