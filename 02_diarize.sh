#!/usr/bin/env bash
# Step 2 — label who says what, and merge onto the MacWhisper transcript.
# Usage: ./02_diarize.sh <audio-for-diarization> <macwhisper.whisper> [output-dir] [extra opts]
#
# IMPORTANT: pass the ORIGINAL (pre-isolation) audio as the first arg. Isolation
# smears voice timbre and hurts speaker separation — diarize the raw recording.
# Extra opts passed through to lib/diarize_merge.py, e.g.:
#   --speakers 3                       (if not 2 people)
#   --labels "Interviewer,Responder"   (names, in order of first appearance)
#   --override "7=Responder,12=Responder"  (force specific segment indices)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIG="${1:?usage: 02_diarize.sh <original-audio> <macwhisper.whisper> [outdir] [opts]}"
WHISPER="${2:?need the MacWhisper .whisper file}"
OUTDIR="${3:-$(cd "$(dirname "$WHISPER")" && pwd)}"
if [ "${3:-}" != "" ]; then shift 3; else shift 2; fi
PY="$DIR/.venv-pyannote/bin/python"
[ -x "$PY" ] || { echo "Run ./setup.sh first."; exit 1; }
[ -s "$HOME/.cache/huggingface/token" ] || { echo "No HF token at ~/.cache/huggingface/token — see README 'HuggingFace access'."; exit 1; }

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
unzip -o -q "$WHISPER" metadata.json -d "$work"
ffmpeg -hide_banner -loglevel error -y -i "$ORIG" -ac 1 -ar 16000 "$work/diar.wav"

HF_HUB_DISABLE_TELEMETRY=1 "$PY" "$DIR/lib/diarize_merge.py" \
  "$work/diar.wav" "$work/metadata.json" "$WHISPER" "$OUTDIR" "$@"
