#!/usr/bin/env bash
# Step 1 — isolate the voices from background noise and lift a quiet speaker.
# Usage: ./01_clean.sh <input-audio> [output-dir]
# Outputs (next to input, or in output-dir):
#   <name>.isolated.mp3            natural leveling  (transcribe THIS in MacWhisper)
#   <name>.isolated_aggressive.mp3 harder denoise + louder quiet speaker
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IN="${1:?usage: 01_clean.sh <input-audio> [output-dir]}"
OUTDIR="${2:-$(cd "$(dirname "$IN")" && pwd)}"
PY="$DIR/.venv-demucs/bin/python"
[ -x "$PY" ] || { echo "Run ./setup.sh first."; exit 1; }

base="$(basename "${IN%.*}")"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

echo "[1/2] Isolating voice (Demucs, ~1 min per 3 min of audio)…"
"$PY" -m demucs --two-stems=vocals -d cpu -o "$work" "$IN" >/dev/null 2>&1
voc="$work/htdemucs/$base/vocals.wav"
[ -f "$voc" ] || { echo "Demucs output not found ($voc)"; exit 1; }

echo "[2/2] Leveling + normalizing…"
ffmpeg -hide_banner -loglevel error -y -i "$voc" -ac 1 \
  -af "highpass=f=75,dynaudnorm=f=300:g=21:p=0.9:m=15:r=0.15,loudnorm=I=-16:TP=-1.5:LRA=13" \
  -c:a libmp3lame -b:a 192k "$OUTDIR/$base.isolated.mp3"
ffmpeg -hide_banner -loglevel error -y -i "$voc" -ac 1 \
  -af "highpass=f=85,afftdn=nr=12,dynaudnorm=f=200:g=15:p=0.9:m=30:r=0.3,loudnorm=I=-16:TP=-1.5:LRA=11" \
  -c:a libmp3lame -b:a 192k "$OUTDIR/$base.isolated_aggressive.mp3"

echo
echo "Done:"
echo "  $OUTDIR/$base.isolated.mp3            <-- transcribe this in MacWhisper, then run 02_diarize.sh"
echo "  $OUTDIR/$base.isolated_aggressive.mp3"
