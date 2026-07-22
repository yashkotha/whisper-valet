#!/usr/bin/env python3
"""Run pyannote speaker diarization and merge the speaker turns onto an existing
MacWhisper transcript (word-level timestamps). Writes .diarized.{txt,srt,whisper}."""
import argparse, json, os, copy, zipfile
from collections import defaultdict
import torch
from pyannote.audio import Pipeline


def bounds(s):
    ws = s.get("words", [])
    if ws:
        return ws[0]["startTime"] / 1000.0, ws[-1]["endTime"] / 1000.0
    return s.get("start", 0) / 1000.0, s.get("end", 0) / 1000.0


def mmss(x):
    return f"{int(x)//60}:{int(x)%60:02d}"


def srt_ts(x):
    ms = int(round(x * 1000)); h = ms // 3600000; m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000; mm = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{mm:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio"); ap.add_argument("metadata")
    ap.add_argument("whisper"); ap.add_argument("outdir")
    ap.add_argument("--speakers", type=int, default=2,
                    help="known number of speakers (0 = let pyannote decide)")
    ap.add_argument("--labels", default="Interviewer,Responder",
                    help="comma-separated names, assigned in order of first appearance")
    ap.add_argument("--override", default="",
                    help='force segment indices, e.g. "7=Responder,12=Responder"')
    ap.add_argument("--model", default="pyannote/speaker-diarization-3.1")
    a = ap.parse_args()

    tok = None
    tp = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(tp):
        tok = open(tp).read().strip()
    tok = tok or os.environ.get("HF_TOKEN")

    try:
        pipe = Pipeline.from_pretrained(a.model, use_auth_token=tok)
    except TypeError:
        pipe = Pipeline.from_pretrained(a.model, token=tok)
    if pipe is None:
        raise SystemExit("Pipeline failed to load — check the HF token and that the "
                         "model licenses are accepted (see README).")
    pipe.to(torch.device("cpu"))

    kwargs = {"num_speakers": a.speakers} if a.speakers > 0 else {}
    diar = pipe(a.audio, **kwargs)
    turns = [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]

    d = json.load(open(a.metadata)); segs = d["transcripts"]

    def assign(st, en):
        ov = defaultdict(float)
        for ts, te, sp in turns:
            ov[sp] += max(0.0, min(en, te) - max(st, ts))
        if ov and max(ov.values()) > 0:
            sp = max(ov, key=ov.get); return sp, ov[sp] / max(en - st, 1e-6)
        c = (st + en) / 2; best = None; bd = 1e9
        for ts, te, sp in turns:
            dd = abs((ts + te) / 2 - c)
            if dd < bd: bd, best = dd, sp
        return best, 0.0

    raw = [assign(*bounds(s)) for s in segs]

    labels = [x.strip() for x in a.labels.split(",")]
    order = []
    for sp, _ in raw:
        if sp not in order:
            order.append(sp)
    name_of = {sp: (labels[i] if i < len(labels) else f"Speaker {i+1}")
               for i, sp in enumerate(order)}

    overrides = {}
    for kv in filter(None, a.override.split(",")):
        k, v = kv.split("="); overrides[int(k)] = v.strip()

    final = []
    for i, (sp, conf) in enumerate(raw):
        if i in overrides:
            final.append((i, overrides[i], conf, "override"))
        else:
            final.append((i, name_of[sp], conf, "acoustic" if conf >= 0.60 else "acoustic?"))

    print(" idx  time   speaker         conf  src         text")
    for (i, r, conf, src), s in zip(final, segs):
        st, _ = bounds(s); flag = "  <-- verify" if src == "acoustic?" else ""
        print(f"[{i:2}] {mmss(st):>5}  {r:15} {conf:4.2f} {src:10}  {s.get('text','').strip()[:42]}{flag}")

    os.makedirs(a.outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.whisper))[0]

    lines = []; prev = None
    for (i, r, conf, src), s in zip(final, segs):
        st, _ = bounds(s); t = s.get("text", "").strip()
        if r != prev:
            lines.append(f"\n[{mmss(st)}] {r}:"); prev = r
        lines.append(f"  {t}")
    open(os.path.join(a.outdir, base + ".diarized.txt"), "w").write("\n".join(lines).strip() + "\n")

    srt = []
    for n, ((i, r, conf, src), s) in enumerate(zip(final, segs), 1):
        st, en = bounds(s)
        srt += [str(n), f"{srt_ts(st)} --> {srt_ts(en)}", f"{r}: {s.get('text','').strip()}", ""]
    open(os.path.join(a.outdir, base + ".diarized.srt"), "w").write("\n".join(srt))

    # rebuild .whisper with corrected speaker fields (reuse existing UUIDs where possible)
    existing = d.get("speakers", [])
    uniq = []
    for (_, r, _, _) in final:
        if r not in uniq:
            uniq.append(r)
    spk_obj = {}
    for idx, r in enumerate(uniq):
        sid = existing[idx]["id"] if idx < len(existing) else f"{idx:08d}-0000-4000-8000-000000000000"
        spk_obj[r] = {"id": sid, "name": r, "color": idx}
    for (i, r, conf, src), s in zip(final, segs):
        s["speaker"] = copy.deepcopy(spk_obj[r])
    d["speakers"] = list(spk_obj.values())
    newmeta = json.dumps(d, ensure_ascii=False)
    outw = os.path.join(a.outdir, base + ".diarized.whisper")
    with zipfile.ZipFile(a.whisper, "r") as zin, zipfile.ZipFile(outw, "w", zipfile.ZIP_STORED) as zo:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "metadata.json":
                data = newmeta.encode("utf-8")
            zo.writestr(it, data)
    print("\nWrote to", a.outdir, "->", base + ".diarized.{txt,srt,whisper}")


if __name__ == "__main__":
    main()
