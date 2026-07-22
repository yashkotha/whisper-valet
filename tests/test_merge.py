"""Unit tests for the diarization-merge logic (pure functions, no torch)."""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "merge_diarization",
    Path(__file__).resolve().parents[1] / "lib" / "merge_diarization.py",
)
merge = importlib.util.module_from_spec(spec)
sys.modules["merge_diarization"] = merge
spec.loader.exec_module(merge)


TURNS = [
    (0.0, 10.0, "SPEAKER_00"),
    (10.5, 12.0, "SPEAKER_01"),
    (12.5, 20.0, "SPEAKER_00"),
]


def test_timestamps():
    assert merge.mmss(0) == "0:00"
    assert merge.mmss(75) == "1:15"
    assert merge.srt_ts(1.5) == "00:00:01,500"
    assert merge.srt_ts(3661.25) == "01:01:01,250"


def test_seg_bounds_ms_to_seconds():
    assert merge.seg_bounds({"start": 1500, "end": 4000}) == (1.5, 4.0)


def test_assign_speaker_overlap_winner():
    sp, conf = merge.assign_speaker(TURNS, 1.0, 5.0)
    assert sp == "SPEAKER_00"
    assert conf > 0.95


def test_assign_speaker_partial_overlap_confidence():
    # segment 9.5-11.5: 0.5s in SPEAKER_00, 1.0s in SPEAKER_01
    sp, conf = merge.assign_speaker(TURNS, 9.5, 11.5)
    assert sp == "SPEAKER_01"
    assert 0.4 < conf < 0.6


def test_assign_speaker_no_overlap_falls_back_to_nearest():
    sp, conf = merge.assign_speaker(TURNS, 25.0, 26.0)
    assert sp == "SPEAKER_00"  # nearest turn center is 12.5-20.0
    assert conf == 0.0


def test_label_by_appearance_exact_match_applies_names():
    raw = [("B", 0.9), ("A", 0.8), ("B", 0.7)]
    final = merge.label_by_appearance(raw, ["Interviewer", "Responder"])
    # B appears first -> Interviewer; A second -> Responder
    assert [r for r, _ in final] == ["Interviewer", "Responder", "Interviewer"]


def test_label_by_appearance_count_mismatch_goes_generic():
    # 3 detected speakers but only 2 labels -> hint dropped, generic names
    raw = [("B", 0.9), ("A", 0.8), ("C", 0.6)]
    final = merge.label_by_appearance(raw, ["Interviewer", "Responder"])
    assert [r for r, _ in final] == ["Speaker 1", "Speaker 2", "Speaker 3"]


def test_label_by_appearance_no_labels_generic_any_count():
    raw = [("X", 1.0)]
    assert merge.label_by_appearance(raw, [])[0][0] == "Speaker 1"
    raw5 = [(f"S{i}", 1.0) for i in range(5)]
    assert [r for r, _ in merge.label_by_appearance(raw5, [])] == \
        [f"Speaker {i+1}" for i in range(5)]


def test_fallback_labels_renames_mw_speakers_on_match():
    segs = [{"speaker": "Speaker 1"}, {"speaker": "Speaker 2"}, {"speaker": "Speaker 1"}]
    final = merge.fallback_labels(segs, ["Host", "Guest"])
    assert [r for r, _ in final] == ["Host", "Guest", "Host"]


def test_fallback_labels_mismatch_keeps_mw_names():
    segs = [{"speaker": "Speaker 1"}, {"speaker": "Speaker 2"}, {"speaker": "Speaker 3"}]
    final = merge.fallback_labels(segs, ["Host", "Guest"])
    assert [r for r, _ in final] == ["Speaker 1", "Speaker 2", "Speaker 3"]


def test_parse_speaker_spec():
    import pytest
    assert merge.parse_speaker_spec("auto") == {}
    assert merge.parse_speaker_spec("0") == {}
    assert merge.parse_speaker_spec("") == {}
    assert merge.parse_speaker_spec("3") == {"num_speakers": 3}
    assert merge.parse_speaker_spec("2-5") == {"min_speakers": 2, "max_speakers": 5}
    for bad in ("abc", "-1", "5-2", "2-", "1.5"):
        with pytest.raises(ValueError):
            merge.parse_speaker_spec(bad)


def test_render_txt_groups_consecutive_speaker():
    segs = [
        {"start": 0, "end": 1000, "text": "Hello."},
        {"start": 1000, "end": 2000, "text": "How are you?"},
        {"start": 2000, "end": 3000, "text": "Fine."},
    ]
    final = [("Interviewer", 1.0), ("Interviewer", 1.0), ("Responder", 1.0)]
    txt = merge.render_txt(final, segs)
    assert txt.count("Interviewer:") == 1  # grouped, not repeated
    assert "Responder:" in txt


def test_render_report_flags_low_confidence():
    segs = [{"start": 0, "end": 1000, "text": "Hi"},
            {"start": 1000, "end": 2000, "text": "Yes"}]
    final = [("A", 0.9), ("B", 0.3)]
    rep = merge.render_report(final, segs, "clip", "engine", 2)
    assert rep.count("<-- verify") == 1
    assert "1 low-confidence" in rep
