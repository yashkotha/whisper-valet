#!/bin/bash
# AudioDrop watcher — invoked by launchd (WatchPaths on the inbox + periodic
# safety net) or manually. Idempotent: rescans the inbox, processes each stable
# new media file exactly once by MOVING it into its own Processed/<clip>/ folder.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$DIR/config.env" ] && source "$DIR/config.env"
: "${INBOX:=$HOME/AudioDrop}"
: "${OUTBOX:=$INBOX/Processed}"
: "${NOTIFY:=1}"
LOG="$DIR/logs/watcher.log"
LOCK="$DIR/logs/.watcher.lock"
mkdir -p "$INBOX" "$OUTBOX" "$DIR/logs"
log(){ printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

# single instance (mkdir is atomic); stale-lock recovery after 6h
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
    log "stale lock removed"; rmdir "$LOCK" 2>/dev/null || exit 0
    mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

notify(){ [ "$NOTIFY" = "1" ] && osascript -e "display notification \"$2\" with title \"Whisper Valet\" subtitle \"$1\"" >/dev/null 2>&1 || true; }

is_media(){ case "$(printf %s "${1##*.}" | tr '[:upper:]' '[:lower:]')" in mp3|wav|m4a|aac|flac|ogg|opus|aiff|aif|mp4|mov|m4v|webm) return 0;; *) return 1;; esac }

while :; do
  found=0
  for f in "$INBOX"/*; do
    [ -f "$f" ] || continue
    is_media "$f" || continue
    name="$(basename "$f")"
    # skip files still being written: size must be stable and >0, no open handles
    s1=$(stat -f %z "$f" 2>/dev/null || echo -1); sleep 3
    s2=$(stat -f %z "$f" 2>/dev/null || echo -2)
    if [ "$s1" != "$s2" ] || [ "$s1" -le 0 ]; then log "skip (still writing): $name"; continue; fi
    if lsof -- "$f" >/dev/null 2>&1; then log "skip (open handle): $name"; continue; fi

    clip="${name%.*}"
    clipdir="$OUTBOX/$clip"
    [ -e "$clipdir" ] && clipdir="$OUTBOX/$clip-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$clipdir"
    mv "$f" "$clipdir/$name" || { log "ERROR: could not move $name"; continue; }
    found=1
    log "processing: $name -> $clipdir"
    notify "$clip" "Processing started"
    if /bin/bash "$DIR/lib/process_file.sh" "$clipdir/$name" "$clipdir" >>"$LOG" 2>&1; then
      log "done: $clip"
      notify "$clip" "Done — transcript + cleaned audio ready"
    else
      log "FAILED: $clip (see $clipdir/pipeline.log)"
      notify "$clip" "FAILED — see pipeline.log"
    fi
  done
  # keep sweeping until a pass finds nothing (files may arrive mid-run)
  [ "$found" = "1" ] && continue
  break
done
exit 0
