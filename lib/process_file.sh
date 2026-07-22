#!/bin/bash
# Full per-clip pipeline: isolate voices -> transcribe (MacWhisper CLI) ->
# diarize (pyannote on the ORIGINAL) -> export transcript files.
# Usage: process_file.sh <original-audio-or-video> <clip-output-dir>
# The original is expected to already live inside <clip-output-dir>.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$DIR/config.env" ] && source "$DIR/config.env"
: "${MW:=/Applications/MacWhisper.app/Contents/MacOS/mw}"
: "${NUM_SPEAKERS:=auto}"
: "${LABELS:=}"
: "${AGGRESSIVE:=1}"

ORIG="${1:?usage: process_file.sh <original> <clipdir>}"
CLIPDIR="${2:?need clip output dir}"
base="$(basename "${ORIG%.*}")"
WORK="$CLIPDIR/.work"
mkdir -p "$WORK"
LOG="$CLIPDIR/pipeline.log"
log(){ printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG" >&2; }
fail(){ log "FAILED: $*"; echo "failed: $*" > "$CLIPDIR/status.txt"; exit 1; }

[ -x "$MW" ] || fail "MacWhisper CLI not found at $MW (install MacWhisper, or set MW= in config.env)"

echo "processing" > "$CLIPDIR/status.txt"
log "=== $base ==="

# 0. Uniform audio input (also handles video containers for demucs)
log "[0/4] extracting audio stream"
ffmpeg -hide_banner -loglevel error -y -i "$ORIG" -vn -ac 2 -ar 44100 "$WORK/input.wav" \
  || fail "ffmpeg could not read the input"

# 1. Demucs voice isolation
log "[1/4] isolating voices (demucs)"
"$DIR/.venv-demucs/bin/python" -m demucs --two-stems=vocals -d cpu -o "$WORK/sep" "$WORK/input.wav" \
  >>"$LOG" 2>&1 || fail "demucs separation"
VOC="$WORK/sep/htdemucs/input/vocals.wav"
[ -f "$VOC" ] || fail "demucs produced no vocals stem"

# 2. Leveled cleaned audio
log "[2/4] leveling cleaned audio"
ffmpeg -hide_banner -loglevel error -y -i "$VOC" -ac 1 \
  -af "highpass=f=75,dynaudnorm=f=300:g=21:p=0.9:m=15:r=0.15,loudnorm=I=-16:TP=-1.5:LRA=13" \
  -c:a libmp3lame -b:a 192k "$CLIPDIR/$base.cleaned.mp3" || fail "ffmpeg leveling"
if [ "$AGGRESSIVE" = "1" ]; then
  ffmpeg -hide_banner -loglevel error -y -i "$VOC" -ac 1 \
    -af "highpass=f=85,afftdn=nr=12,dynaudnorm=f=200:g=15:p=0.9:m=30:r=0.3,loudnorm=I=-16:TP=-1.5:LRA=11" \
    -c:a libmp3lame -b:a 192k "$CLIPDIR/$base.cleaned_aggressive.mp3" || log "aggressive variant failed (non-fatal)"
fi

# 3. Transcribe the CLEANED audio (MacWhisper CLI, word timestamps + fallback speakers)
log "[3/4] transcribing (MacWhisper CLI)"
"$MW" transcribe "$CLIPDIR/$base.cleaned.mp3" --format json --speakers \
  --output-dir "$WORK" --overwrite >>"$LOG" 2>&1 || fail "mw transcribe"
MWJSON="$WORK/$base.cleaned.json"
[ -f "$MWJSON" ] || fail "mw produced no JSON"

# 4. Diarize the ORIGINAL audio (intact timbre) + merge labels onto the transcript
log "[4/4] diarizing (pyannote on original) + exporting"
ffmpeg -hide_banner -loglevel error -y -i "$ORIG" -ac 1 -ar 16000 "$WORK/diar.wav" || fail "ffmpeg 16k"
HF_HUB_DISABLE_TELEMETRY=1 "$DIR/.venv-pyannote/bin/python" "$DIR/lib/merge_diarization.py" \
  "$MWJSON" "$WORK/diar.wav" "$CLIPDIR" \
  --clip "$base" --speakers "$NUM_SPEAKERS" --labels "$LABELS" \
  >>"$LOG" 2>&1 || fail "diarization/merge"

rm -rf "$WORK"
echo "done" > "$CLIPDIR/status.txt"
log "=== done: $CLIPDIR ==="
