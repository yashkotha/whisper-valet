#!/usr/bin/env python3
"""Diarize with pyannote (on the original-timbre audio) and merge speaker labels
onto a MacWhisper CLI JSON transcript (word-level timestamps, ms).

Graceful degradation: if pyannote can't run (missing/revoked HF token, network,
model gating), falls back to MacWhisper's own per-segment speaker labels so the
pipeline still produces a labeled transcript.

Outputs into <outdir>: transcript.txt, transcript.srt, transcript.json, report.txt

Runs inside the pinned pyannote venv. Heavy imports are lazy so the pure
helpers below are unit-testable without torch installed.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

CONFIDENCE_FLOOR = 0.60  # below this a segment gets a "verify" flag


# ---------- pure helpers (unit-tested) ----------

def mmss(x: float) -> str:
    return f"{int(x)//60}:{int(x)%60:02d}"


def srt_ts(x: float) -> str:
    ms = int(round(x * 1000))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    mm = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{mm:03d}"


def seg_bounds(seg: dict) -> tuple[float, float]:
    """Segment start/end in seconds from a MacWhisper JSON segment (ms fields)."""
    return seg.get("start", 0) / 1000.0, seg.get("end", 0) / 1000.0


def assign_speaker(turns, st: float, en: float):
    """Pick the diarization speaker with the largest time-overlap with [st, en].

    Returns (speaker, confidence) where confidence is overlap/segment-duration.
    Falls back to the nearest turn (confidence 0) when nothing overlaps.
    """
    ov = defaultdict(float)
    for ts, te, sp in turns:
        ov[sp] += max(0.0, min(en, te) - max(st, ts))
    if ov and max(ov.values()) > 0:
        sp = max(ov, key=ov.get)
        return sp, ov[sp] / max(en - st, 1e-6)
    c = (st + en) / 2
    best, bd = None, float("inf")
    for ts, te, sp in turns:
        dd = abs((ts + te) / 2 - c)
        if dd < bd:
            bd, best = dd, sp
    return best, 0.0


def parse_speaker_spec(spec):
    """Parse a speaker-count spec into pyannote kwargs.

    ``auto``/``0``/empty -> {} (pyannote estimates the count per clip)
    ``N``               -> {"num_speakers": N} (force exactly N)
    ``MIN-MAX``         -> {"min_speakers": MIN, "max_speakers": MAX}
    Raises ValueError on anything else.
    """
    s = str(spec).strip().lower()
    if s in ("", "auto", "0"):
        return {}
    if "-" in s:
        lo, _, hi = s.partition("-")
        if lo.isdigit() and hi.isdigit() and 0 < int(lo) <= int(hi):
            return {"min_speakers": int(lo), "max_speakers": int(hi)}
        raise ValueError(f"invalid speaker range: {spec!r} (use MIN-MAX, e.g. 2-5)")
    if s.isdigit() and int(s) > 0:
        return {"num_speakers": int(s)}
    raise ValueError(f"invalid speaker spec: {spec!r} (use auto, N, or MIN-MAX)")


def label_by_appearance(raw, labels):
    """Map opaque diarization speaker ids to human names in order of first
    appearance. raw is a list of (speaker_id, confidence).

    The label list is a HINT: it is applied only when the number of detected
    speakers exactly matches the number of labels — otherwise every speaker
    gets a generic "Speaker N" name. This keeps custom names (e.g.
    "Interviewer,Responder") from mislabeling a clip that turned out to have a
    different speaker count.
    """
    order = []
    for sp, _ in raw:
        if sp not in order:
            order.append(sp)
    use_labels = labels if labels and len(labels) == len(order) else []
    name_of = {sp: (use_labels[i] if use_labels else f"Speaker {i+1}")
               for i, sp in enumerate(order)}
    return [(name_of[sp], conf) for sp, conf in raw]


def fallback_labels(segs, labels):
    """Use MacWhisper's own per-segment speakers. Same count-match hint rule
    as label_by_appearance."""
    order = []
    for s in segs:
        sp = s.get("speaker") or "Speaker 1"
        if sp not in order:
            order.append(sp)
    use_labels = labels if labels and len(labels) == len(order) else []
    name_of = {sp: (use_labels[i] if use_labels else sp) for i, sp in enumerate(order)}
    return [(name_of.get(s.get("speaker") or "Speaker 1"), 0.5) for s in segs]


def render_txt(final, segs) -> str:
    lines, prev = [], None
    for (r, _conf), s in zip(final, segs, strict=True):
        st, _ = seg_bounds(s)
        t = (s.get("text") or "").strip()
        if r != prev:
            lines.append(f"\n[{mmss(st)}] {r}:")
            prev = r
        lines.append(f"  {t}")
    return "\n".join(lines).strip() + "\n"


def render_srt(final, segs) -> str:
    srt = []
    for n, ((r, _conf), s) in enumerate(zip(final, segs, strict=True), 1):
        st, en = seg_bounds(s)
        srt += [str(n), f"{srt_ts(st)} --> {srt_ts(en)}",
                f"{r}: {(s.get('text') or '').strip()}", ""]
    return "\n".join(srt)


def render_report(final, segs, clip: str, engine: str, speaker_spec: str) -> str:
    detected = len({r for r, _ in final})
    rep = [f"clip: {clip}", f"diarization: {engine}",
           f"speakers: {speaker_spec or 'auto'} (detected {detected})", ""]
    rep.append(" idx  time    speaker          conf  text")
    n_verify = 0
    for i, ((r, conf), s) in enumerate(zip(final, segs, strict=True)):
        st, _ = seg_bounds(s)
        flag = "  <-- verify" if conf < CONFIDENCE_FLOOR else ""
        if flag:
            n_verify += 1
        rep.append(f"[{i:2}] {mmss(st):>6}  {r:15} {conf:4.2f}  "
                   f"{(s.get('text') or '').strip()[:60]}{flag}")
    rep += ["", f"{n_verify} low-confidence segment(s) flagged for a quick listen."
            if n_verify else "All segments assigned with good confidence."]
    return "\n".join(rep) + "\n"


# ---------- pyannote (heavy, lazy) ----------

def run_pyannote(wav: str, speaker_spec: str):
    kwargs = parse_speaker_spec(speaker_spec)
    tok = None
    tp = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(tp):
        tok = open(tp).read().strip()
    tok = tok or os.environ.get("HF_TOKEN")
    import torch
    from pyannote.audio import Pipeline
    try:
        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=tok)
    except TypeError:
        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=tok)
    if pipe is None:
        raise RuntimeError("pipeline load returned None (gating/token)")
    pipe.to(torch.device("cpu"))
    diar = pipe(wav, **kwargs)
    return [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mw_json")
    ap.add_argument("diar_wav")
    ap.add_argument("outdir")
    ap.add_argument("--clip", default="clip")
    ap.add_argument("--speakers", default="auto",
                    help="auto (default), N to force exactly N, or MIN-MAX range")
    ap.add_argument("--labels", default="",
                    help="comma-separated names; applied only when the detected "
                         "speaker count matches the label count")
    a = ap.parse_args()
    parse_speaker_spec(a.speakers)  # fail fast on a bad spec

    d = json.load(open(a.mw_json))
    segs = d.get("segments", [])
    if not segs:
        print("no segments in mw JSON", file=sys.stderr)
        sys.exit(1)

    labels = [x.strip() for x in a.labels.split(",") if x.strip()]
    engine = "pyannote-3.1 (on original audio)"
    try:
        turns = run_pyannote(a.diar_wav, a.speakers)
        raw = [assign_speaker(turns, *seg_bounds(s)) for s in segs]
        final = label_by_appearance(raw, labels)
    except Exception as e:  # noqa: BLE001 — any pyannote failure degrades gracefully
        engine = f"macwhisper-builtin (pyannote unavailable: {type(e).__name__}: {str(e)[:120]})"
        print(f"WARN pyannote failed, falling back to mw speakers: {e}", file=sys.stderr)
        final = fallback_labels(segs, labels)

    os.makedirs(a.outdir, exist_ok=True)
    open(os.path.join(a.outdir, "transcript.txt"), "w").write(render_txt(final, segs))
    open(os.path.join(a.outdir, "transcript.srt"), "w").write(render_srt(final, segs))
    out = {
        "clip": a.clip,
        "diarization_engine": engine,
        "transcription_engine": "MacWhisper CLI (mw), model per app selection",
        "segments": [
            {"start_ms": s.get("start"), "end_ms": s.get("end"),
             "speaker": r, "confidence": round(conf, 3),
             "text": (s.get("text") or "").strip(),
             "words": s.get("words", [])}
            for (r, conf), s in zip(final, segs, strict=True)
        ],
    }
    json.dump(out, open(os.path.join(a.outdir, "transcript.json"), "w"),
              ensure_ascii=False, indent=1)
    open(os.path.join(a.outdir, "report.txt"), "w").write(
        render_report(final, segs, a.clip, engine, a.speakers))
    print(f"exported transcript.{{txt,srt,json}} + report.txt ({engine})")


if __name__ == "__main__":
    main()
