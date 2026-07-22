#!/usr/bin/env bash
# AudioDrop installer. Idempotent — safe to re-run after updates.
#
#   ./setup.sh              full install (venvs + config + daemon + MCP registration)
#   ./setup.sh --no-daemon  skip the launchd watch-folder daemon
#   ./setup.sh --no-mcp     skip Claude Code MCP registration
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_DAEMON=0; NO_MCP=0
for a in "$@"; do case "$a" in --no-daemon) NO_DAEMON=1;; --no-mcp) NO_MCP=1;; esac; done

say(){ printf '\n== %s ==\n' "$*"; }
need(){ command -v "$1" >/dev/null || { echo "ERROR: '$1' not found. $2"; exit 1; }; }

say "checking prerequisites"
need uv "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
need ffmpeg "Install: brew install ffmpeg"
MW_DEFAULT="/Applications/MacWhisper.app/Contents/MacOS/mw"
if [ ! -x "$MW_DEFAULT" ]; then
  echo "WARNING: MacWhisper not found at /Applications/MacWhisper.app."
  echo "         Install it (Pro needed for JSON + speaker features):"
  echo "         brew install --cask macwhisper   — then re-run setup, or set MW= in config.env."
fi
case "$DIR" in
  "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*)
    echo "WARNING: AudioDrop lives in a macOS-protected folder ($DIR)."
    echo "         The launchd daemon cannot read Documents/Desktop/Downloads without"
    echo "         Full Disk Access. Move the repo (e.g. ~/whisper-valet) and re-run." ;;
esac

say "building Python environments (uv, cached — fast on re-runs)"
uv venv "$DIR/.venv-demucs" --python 3.11 --allow-existing >/dev/null
uv pip install --python "$DIR/.venv-demucs/bin/python" -q demucs numpy soundfile
uv venv "$DIR/.venv-pyannote" --python 3.11 --allow-existing >/dev/null
uv pip install --python "$DIR/.venv-pyannote/bin/python" -q \
  "torch==2.2.2" "torchaudio==2.2.2" "pyannote.audio==3.1.1" \
  "huggingface_hub==0.25.2" "numpy<2" soundfile
uv venv "$DIR/.venv-mcp" --python 3.11 --allow-existing >/dev/null
uv pip install --python "$DIR/.venv-mcp/bin/python" -q -e "$DIR"

say "configuration"
if [ ! -f "$DIR/config.env" ]; then
  cp "$DIR/config.example.env" "$DIR/config.env"
  echo "created config.env (defaults: inbox ~/AudioDrop, 2 speakers)"
else
  echo "config.env exists — kept"
fi
# shellcheck disable=SC1091
source "$DIR/config.env" 2>/dev/null || true
INBOX="${INBOX:-$HOME/AudioDrop}"
mkdir -p "$INBOX" "${OUTBOX:-$INBOX/Processed}" "$DIR/logs"

if [ "$NO_DAEMON" = "0" ]; then
  say "installing launchd watch-folder daemon"
  PLIST="$HOME/Library/LaunchAgents/com.whispervalet.watcher.plist"
  sed -e "s|__PIPE_DIR__|$DIR|g" -e "s|__INBOX__|$INBOX|g" -e "s|__HOME__|$HOME|g" \
    "$DIR/launchd/com.whispervalet.watcher.plist.template" > "$PLIST"
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl enable "gui/$(id -u)/com.whispervalet.watcher"
  echo "daemon loaded: com.whispervalet.watcher (watching $INBOX)"
fi

if [ "$NO_MCP" = "0" ]; then
  say "registering MCP server with Claude Code"
  if command -v claude >/dev/null; then
    claude mcp remove whisper-valet -s user 2>/dev/null || true
    claude mcp add -s user whisper-valet -- "$DIR/.venv-mcp/bin/whisper-valet-mcp"
    echo "registered as 'whisper-valet' (user scope)"
  else
    echo "claude CLI not found — register manually in your MCP client config:"
    echo "  command: $DIR/.venv-mcp/bin/whisper-valet-mcp"
  fi
fi

say "speaker diarization (optional but recommended)"
if [ -s "$HOME/.cache/huggingface/token" ]; then
  echo "HuggingFace token present — pyannote diarization active."
else
  cat <<'EOF'
No HuggingFace token found. Without it, speaker labeling falls back to
MacWhisper's built-in detection (works, but weaker on hard audio). To enable
pyannote (better):
  1. Accept the licenses (free) on BOTH model pages, logged into one account:
       https://huggingface.co/pyannote/segmentation-3.0
       https://huggingface.co/pyannote/speaker-diarization-3.1
  2. Create a Read token: https://huggingface.co/settings/tokens
  3. mkdir -p ~/.cache/huggingface && printf '%s' 'hf_YOUR_TOKEN' > ~/.cache/huggingface/token
EOF
fi

say "done"
echo "Drop an audio file into: $INBOX"
echo "Results appear in:       ${OUTBOX:-$INBOX/Processed}/<clip>/"
