"""
selftest.py — offline check of the episode-browser DATA LAYER (no UI, no real data).

Exercises the pieces the browser stands on, so a broken data layer fails here rather
than inside Streamlit:
  * JSONL and (if pyarrow present) Parquet round-trip preserve nested fields exactly
  * duration_s is derived, never persisted
  * schema + state-model-registry validation catches the key invariants
  * coverage tiling produces gaps + a sane completeness metric

Run:  python selftest.py        # -> prints PASS/FAIL, sets exit code
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from utils import (annotations, coverage, episode_io, query, validation,  # noqa: E402
                   video_preview, weather, wiser_tracks)

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILS.append(msg)


def sample_episodes() -> list[dict]:
    return [
        {
            "episode_id": "t1", "schema_version": 1, "state_model_id": "synthetic_v0",
            "level": "per_animal", "subject_ids": ["12378"],
            "t_start": 1000, "t_end": 4000,
            "state_vector": {"x": 1.0, "y": 2.0, "speed": 0.5, "dyad_distance": 30.0},
            "zones": {"shelter_A": 0.7, "open": 0.3},
            "source_streams": ["WISER", "video"],
            "lens_scores": {"self_surprise": 0.8},
        },
        {
            "episode_id": "t2", "schema_version": 1, "state_model_id": "synthetic_v0",
            "level": "per_animal", "subject_ids": ["12378"],
            "t_start": 6000, "t_end": 10000,
            "state_vector": {"x": 5.0, "y": 6.0, "speed": 3.0, "dyad_distance": 12.0},
            "source_streams": ["WISER"],
            "lens_scores": None,   # absence is first-class
        },
    ]


def main() -> int:
    print("[1] validation — valid episodes pass")
    eps = sample_episodes()
    rep = validation.validate_all(eps)
    check(rep.ok, f"valid set -> {rep.summary()}")

    print("[2] validation — bad state_model_id is caught")
    bad = [dict(eps[0], episode_id="bad", state_model_id="does_not_exist")]
    rep_bad = validation.validate_all(bad)
    check(not rep_bad.ok, "unregistered state_model_id -> FAIL as expected")

    print("[3] validation — zone-as-feature rule")
    schema = validation.load_schema()
    reg = {"state_models": {"m": {"features": ["zones"], "is_synthetic": False}}}
    rr = validation.validate_registry(reg)
    check(not rr.ok, "zone feature without zone_is_feature -> FAIL as expected")

    print("[4] JSONL round-trip preserves nested fields")
    df = pd.DataFrame(eps)
    tmp = HERE / "data" / "_selftest.jsonl"
    episode_io.write_jsonl(df, tmp)
    back = episode_io.read_jsonl(tmp)
    check(back.loc[0, "zones"] == {"shelter_A": 0.7, "open": 0.3}, "zones map survived JSONL")
    check("duration_s" in back.columns and abs(back.loc[0, "duration_s"] - 3.0) < 1e-9,
          "duration_s derived at load (=3.0s)")
    tmp.unlink(missing_ok=True)

    print("[5] Parquet round-trip (skipped if pyarrow absent)")
    try:
        tmpq = HERE / "data" / "_selftest.parquet"
        episode_io.write_parquet(df, tmpq)
        backq = episode_io.read_parquet(tmpq)
        check(backq.loc[0, "lens_scores"] == {"self_surprise": 0.8}, "lens_scores map survived Parquet")
        check(pd.isna(backq.loc[1, "lens_scores"]) or backq.loc[1, "lens_scores"] is None,
              "absent lens_scores stays absent (not 0)")
        tmpq.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  [skip] pyarrow unavailable ({type(exc).__name__})")

    print("[6] coverage tiling + gaps")
    cov = coverage.compute_coverage(episode_io._derive_duration(df))
    key = ("12378", "per_animal")
    check(key in cov, "subject/level lane present")
    gaps = [iv for iv in cov[key]["intervals"] if iv.kind == "gap"]
    check(any(g.t_start == 4000 and g.t_end == 6000 for g in gaps),
          "un-episoded [4000,6000] rendered as a gap")
    check(0.0 < cov[key]["pct_tiled"] < 100.0, "pct_tiled between 0 and 100")

    print("[7] query — lens ranking never invents a 0, absence stays absent")
    fdf = episode_io._derive_duration(df)
    ranked = query.rank_by_lens(fdf, "self_surprise", drop_absent=True)
    check(len(ranked) == 1 and ranked.iloc[0]["episode_id"] == "t1",
          "rank_by_lens(self_surprise) keeps only the scored episode")
    # range filter with include_absent=False must drop the unscored episode
    only_scored = query.filter_episodes(fdf, lens_key="self_surprise",
                                        lens_min=0.5, lens_max=1.0, include_absent_lens=False)
    check(set(only_scored["episode_id"]) == {"t1"}, "score range filter excludes absent-score episode")
    with_absent = query.filter_episodes(fdf, lens_key="self_surprise",
                                        lens_min=0.5, lens_max=1.0, include_absent_lens=True)
    check("t2" in set(with_absent["episode_id"]), "include_absent=True keeps the absent-score episode")

    # text_search: matches on resolved name, is AND over tokens, empty -> all.
    nmap = {"12378": "Siesta"}
    check(set(query.text_search(fdf, "Siesta", nmap)["episode_id"]) == {"t1", "t2"},
          "text_search resolves tag 12378 -> 'Siesta'")
    check(set(query.text_search(fdf, "shelter_A", nmap)["episode_id"]) == {"t1"},
          "text_search matches a zone label")
    check(len(query.text_search(fdf, "", nmap)) == len(fdf), "empty query returns everything")
    check(len(query.text_search(fdf, "Siesta zzzz", nmap)) == 0, "AND semantics: unmet token -> no match")

    print("[8] annotations — append-only, timestamped, no overwrite")
    p1 = annotations.write_annotation("t1", "tester", "interesting", ["rest_like"], "n1", session="_selftest")
    p2 = annotations.write_annotation("t2", "tester", "artifact", [], "n2", session="_selftest")
    check(p1 == p2, "same session appends to one file")
    log = annotations.read_log(p1)
    check(len(log) >= 2 and log[-1]["episode_id"] == "t2", "annotations read back in order")
    pe = annotations.log_blind_eval("t1", "tester", "self_surprise", "interesting",
                                    {"self_surprise": 0.8}, session="_selftest")
    check(annotations.read_log(pe)[-1]["ranking_method"] == "self_surprise", "blind-eval log records ranking_method")
    p1.unlink(missing_ok=True)
    pe.unlink(missing_ok=True)

    print("[9] video_preview — resolve, open-file guard, extraction (skips if no ffmpeg)")
    ep_vid = {"linked_assets": {"video_path": "data/sample_clip.mp4",
                                "video_t_offset_s": 5.0, "preview_span_s": 6.0, "synthetic": True}}
    v = video_preview.resolve_video(ep_vid, base_dir=HERE)
    check(v is not None and abs(v["start_s"] - 5.0) < 1e-9 and abs(v["end_s"] - 11.0) < 1e-9,
          "resolve_video maps offset+span -> [5.0, 11.0]")
    check(video_preview.resolve_video({"linked_assets": None}) is None, "no link -> None")
    # Open Reolink hour (no _to_) must be refused; a closed one accepted.
    check(not video_preview.is_closed_recording("CH05_2026-06-30_21-00-00.mp4"),
          "open Reolink hour flagged not-closed")
    check(video_preview.is_closed_recording("CH05_2026-06-30_21-00-00_to_22-00-00.mp4"),
          "closed Reolink hour accepted")
    check(video_preview.extract_frames("CH05_2026-06-30_21-00-00.mp4", 0, 6) == [],
          "extract refuses an open recording")
    if video_preview.find_ffmpeg():
        clip = video_preview.ensure_sample_clip()
        check(clip is not None and clip.exists(), "ensure_sample_clip created a clip")
        frames = video_preview.extract_frames(clip, 5.0, 11.0, n=4, width=160)
        check(len(frames) == 4 and all(f["png"][:4] == b"\x89PNG" for f in frames),
              "extracted 4 downscaled PNG frames")
    else:
        print("  [skip] ffmpeg not found — preview extraction not exercised")

    print("[10] weather — graceful when dir absent; slice/nearest on a tiny frame")
    empty = weather.load_weather(HERE / "data" / "_no_such_weather_dir")
    check(empty.empty and "temp_c" in empty.columns, "missing weather dir -> empty typed frame")
    wtiny = pd.DataFrame({"ts": pd.to_datetime(["2026-06-30 21:00", "2026-06-30 21:30",
                                                "2026-06-30 22:00"]),
                          "temp_c": [25.0, 24.0, 23.0], "rain_mm_hr": [0.0, 1.2, 0.0]})
    sl = weather.slice_window(wtiny, pd.Timestamp("2026-06-30 21:10"), pd.Timestamp("2026-06-30 21:50"))
    check(len(sl) == 1 and sl.iloc[0]["temp_c"] == 24.0, "slice_window keeps the in-window sample")
    near = weather.nearest(wtiny, pd.Timestamp("2026-06-30 21:58"))
    check(near is not None and near["temp_c"] == 23.0, "nearest picks the closest sample")
    check(weather.nearest(empty, pd.Timestamp("2026-06-30 21:00")) is None, "nearest on empty -> None")

    print("[11] wiser_tracks — graceful when day-file absent; window filter + downsample")
    check(wiser_tracks.read_day("1900-01-01").empty, "missing WISER day-file -> empty frame")
    dates = wiser_tracks.candidate_dates(int(pd.Timestamp("2026-06-30T21:00:00Z").value // 1e6),
                                         int(pd.Timestamp("2026-06-30T22:00:00Z").value // 1e6))
    check(dates and dates[0] == "2026-06-30", "candidate_dates leads with the window's EDT date")
    day = pd.DataFrame({"shortid": [12378, 12378, 12386], "x": [1.0, 2.0, 9.0],
                        "y": [1.0, 2.0, 9.0], "ts": [1000, 2000, 1500], "calc_err": [0, 0, 0]})
    win = wiser_tracks.filter_window(day, 900, 1600, {"12378": "Siesta"}, {"12378"}, max_per_rat=10)
    check(set(win["episode_id"] if "episode_id" in win else win["rat"]) == {"Siesta"},
          "filter_window keeps only requested tag, resolves name")
    check(len(win) == 1, "filter_window respects the time window")
    lm = wiser_tracks.load_landmarks()
    check(set(lm.keys()) == {"rects", "points"} and list(lm["rects"].columns)[:2] == ["name", "type"],
          "load_landmarks returns rects+points frames (WISER inch frame)")

    print()
    if FAILS:
        print(f"FAIL — {len(FAILS)} check(s) failed")
        return 1
    print("PASS — data layer healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
