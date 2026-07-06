"""Offline self-test for the audio feature extraction (no field data, no ffmpeg).

Pushes synthetic silence / tone / noise through features.py + qc.py and asserts the
results are sane and crash-free. Exit 0 = PASS, 1 = FAIL.

    C:\\Users\\Cornell\\miniforge3\\envs\\audio\\python.exe scripts/selftest_features.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.features import compute_features, DBFS_FLOOR
from src import qc
from src.audio_io import _dedup_overlapping
from src.time_utils import RecordingFile

CFG = yaml.safe_load((PROJECT_ROOT / "configs" / "audio_analysis.yaml").read_text())
SR = int(CFG["sample_rate_hz"])
WIN = int(CFG["window_s"] * SR)


def _check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main() -> int:
    rng = np.random.default_rng(0)
    ok = True

    # 1) digital silence -> SILENT, finite floor levels
    silence = np.zeros(WIN, dtype=np.float32)
    f = compute_features(silence, SR, CFG)
    ok &= _check("silence: Leq is the finite floor",
                 math.isfinite(f["leq_dbfs_relative"]) and f["leq_dbfs_relative"] <= CFG["silence_dbfs"])
    flag = qc.classify_window(decode_error=False, audio_duration_s=WIN / SR,
                              window_s=CFG["window_s"], min_window_s=CFG["min_window_s"],
                              is_partial=False, before_mic_on=False,
                              leq_dbfs=f["leq_dbfs_relative"], silence_dbfs=CFG["silence_dbfs"],
                              clipped=f["clipped"], gap_before=False)
    ok &= _check("silence: classified 'silent'", flag == qc.SILENT)

    # 2) 3 kHz tone (in the 2-8 kHz biophony band) at -20 dBFS
    t = np.arange(WIN) / SR
    tone = (0.1 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)  # 0.1 amp ~ -20 dBFS
    f = compute_features(tone, SR, CFG)
    ok &= _check("tone: Leq finite and well above silence",
                 math.isfinite(f["leq_dbfs_relative"]) and f["leq_dbfs_relative"] > CFG["silence_dbfs"] + 20)
    ok &= _check("tone: energy concentrated in 2-8 kHz band",
                 f["band_2_8k_db"] > f["band_0_1k_db"] and f["band_2_8k_db"] > f["band_1_2k_db"])
    ok &= _check("tone: spectral centroid near 3 kHz",
                 2200 < f["centroid_hz"] < 3800)
    ok &= _check("tone: Leq sane (between floor and 0 dBFS)",
                 DBFS_FLOOR < f["leq_dbfs_relative"] < 0)

    # 3) white noise -> indices computed (finite) OR gracefully NaN, never a crash
    noise = (0.05 * rng.standard_normal(WIN)).astype(np.float32)
    f = compute_features(noise, SR, CFG)
    for idx in ("aci", "bi_2_8k_camera", "ndsi_1_2k_vs_2_8k_camera", "adi"):
        v = f[idx]
        ok &= _check(f"noise: index '{idx}' is float (finite or NaN, no crash)",
                     isinstance(v, float))

    # 4) clipping detection
    clipped = np.ones(WIN, dtype=np.float32)
    f = compute_features(clipped, SR, CFG)
    ok &= _check("full-scale signal: clipped flag set", f["clipped"] is True)

    # 5) discovery dedup: nested/overlapping backfill segments are dropped, the RTSP
    #    hour-aligned chain and gap-filling segments are kept.
    def _rf(s, e):  # s, e = "HH:MM:SS" on 2026-06-29
        d = "2026-06-29 "
        return RecordingFile(path=Path(f"CH01_2026-06-29_{s.replace(':','-')}_to_{e.replace(':','-')}.mp4"),
                             channel="CH01",
                             start=datetime.fromisoformat(d + s),
                             end=datetime.fromisoformat(d + e))
    scenario = [
        _rf("14:00:00", "15:00:04"),   # real RTSP hourly file
        _rf("14:20:52", "14:41:18"),   # NVR backfill, fully nested -> DROP
        _rf("14:41:18", "14:59:59"),   # NVR backfill, fully nested -> DROP
        _rf("15:20:24", "15:35:20"),   # gap filler beyond RTSP coverage -> KEEP
    ]
    kept, dropped = _dedup_overlapping(scenario)
    kept_names = {r.path.name for r in kept}
    dropped_names = {r.path.name for r in dropped}
    ok &= _check("dedup: two nested backfill segments dropped",
                 dropped_names == {"CH01_2026-06-29_14-20-52_to_14-41-18.mp4",
                                   "CH01_2026-06-29_14-41-18_to_14-59-59.mp4"})
    ok &= _check("dedup: RTSP hourly file + gap filler kept",
                 kept_names == {"CH01_2026-06-29_14-00-00_to_15-00-04.mp4",
                                "CH01_2026-06-29_15-20-24_to_15-35-20.mp4"})

    # 6) a plain contiguous chain is returned unchanged (nothing nests)
    chain = [_rf("12:00:00", "13:00:01"), _rf("13:00:01", "13:17:16"), _rf("13:17:16", "14:00:00")]
    kept_chain, dropped_chain = _dedup_overlapping(chain)
    ok &= _check("dedup: contiguous chain untouched",
                 len(kept_chain) == 3 and not dropped_chain)

    # 7) biophony heuristic: broadband guard + rain suppression
    from src.plotting import biophony_active
    idx = pd.date_range("2026-06-30 00:00", periods=6, freq="h")
    floor = -84.0
    # birds: 2-8 kHz well above its floor, ambient flat near its floor -> all active
    bird_up = pd.Series([floor, floor, -66, -66, -66, floor], index=idx)
    amb_flat = pd.Series([-69, -69, -69, -69, -69, -69], index=idx)
    a_birds = biophony_active(bird_up, amb_flat)
    ok &= _check("biophony: birds (bird up, ambient flat) -> active", bool(a_birds[2] and a_birds[3]))
    # broadband weather: both bands rise together by the same amount -> NOT biophony
    bird_bb = pd.Series([floor, floor, -66, -66, -66, floor], index=idx)
    amb_bb = pd.Series([-69, -69, -51, -51, -51, -69], index=idx)   # ambient rises +18 too
    a_bb = biophony_active(bird_bb, amb_bb)
    ok &= _check("biophony: broadband event (both bands rise) -> rejected", not a_bb.any())
    # rain suppression: a would-be birds window but rain>0 -> suppressed
    rain = pd.Series([0, 0, 0.0, 5.0, 0, 0], index=idx)
    a_rain = biophony_active(bird_up, amb_flat, rain=rain)
    ok &= _check("biophony: rain>0 suppresses shading", (not a_rain[3]) and bool(a_rain[2]))

    print(f"\nSELF-TEST: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
