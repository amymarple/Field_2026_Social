"""Offline self-test for the audio feature extraction (no field data, no ffmpeg).

Pushes synthetic silence / tone / noise through features.py + qc.py and asserts the
results are sane and crash-free. Exit 0 = PASS, 1 = FAIL.

    C:\\Users\\Cornell\\miniforge3\\envs\\audio\\python.exe scripts/selftest_features.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.features import compute_features, DBFS_FLOOR
from src import qc

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

    print(f"\nSELF-TEST: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
