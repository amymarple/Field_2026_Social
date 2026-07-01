"""Lightweight, resumable environmental-audio feature extraction.

Streams audio out of Reolink hourly MP4s (via ffmpeg, never loading a whole hour),
computes per-window RELATIVE camera-mic level + band-limited soundscape indices, and
writes compact, timestamped CSVs (one per channel/date) plus a metadata JSON sidecar.
Designed to run on the field PC and hand the CSVs to the main analysis computer.

Example:
    python scripts/extract_audio_features.py --channel CH01 --date 2026-06-29 --hours 12-13
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.audio_io import DecodeError, find_recording_files, iter_windows
from src.features import compute_features, spectral_and_indices
from src import qc
from src.time_utils import fmt, window_timestamp

# Fixed CSV schema (transfer-ready). index_notes is a trailing diagnostic column.
COLUMNS = [
    "window_start_timestamp", "window_end_timestamp", "channel", "source_file",
    "file_start_timestamp", "file_offset_start_s", "file_offset_end_s", "window_s",
    "sample_rate_hz", "n_samples", "audio_duration_s",
    "leq_dbfs_relative", "l10_dbfs_relative", "l50_dbfs_relative", "l90_dbfs_relative",
    "peak_dbfs_relative", "band_0_1k_db", "band_1_2k_db", "band_2_8k_db",
    "centroid_hz", "rolloff_hz", "aci", "bi_2_8k_camera",
    "ndsi_1_2k_vs_2_8k_camera", "adi", "n_silent_subframes", "clipped",
    "valid_audio", "qc_flag", "index_notes",
]
NAN = float("nan")
GAP_TOLERANCE_S = 5.0  # filename start vs previous end beyond this => timeline_gap


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT),
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _lib_versions() -> dict:
    out = {}
    for mod in ("numpy", "scipy", "librosa", "maad", "soundfile"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception as e:
            out[mod] = f"unavailable ({e})"
    return out


def _empty_features() -> dict:
    """All feature fields as NaN/defaults (for skipped/short/decode-error windows)."""
    f = {c: NAN for c in COLUMNS}
    f["n_silent_subframes"] = 0
    f["clipped"] = False
    return f


def _build_config(args) -> dict:
    cfg = yaml.safe_load(Path(args.config).read_text())
    if args.input_root:
        cfg["input_root"] = args.input_root
    return cfg


def _resolve_window(args, cfg):
    """Return (date_filter, start_dt, end_dt) from --date/--hours/--start/--end."""
    start_dt = datetime.fromisoformat(args.start) if args.start else None
    end_dt = datetime.fromisoformat(args.end) if args.end else None
    date_filter = args.date
    if args.hours:
        if not args.date:
            raise SystemExit("--hours requires --date")
        sh, eh = args.hours.split("-")
        start_dt = datetime.strptime(args.date, "%Y-%m-%d") + timedelta(hours=int(sh))
        end_dt = datetime.strptime(args.date, "%Y-%m-%d") + timedelta(hours=int(eh))
        date_filter = None  # start/end already constrain it
    return date_filter, start_dt, end_dt


def _process_file(rf, cfg, mic_on, gap_before, writer):
    """Stream one file -> feature rows -> writer. Returns (n_rows, n_valid)."""
    sr = int(cfg["sample_rate_hz"])
    window_s = float(cfg["window_s"])
    min_window_s = float(cfg["min_window_s"])
    process_partial = bool(cfg["process_partial_final_window"])
    n_rows = n_valid = 0
    first = True
    try:
        windows = iter_windows(cfg["ffmpeg"], rf, sr, window_s)
        for w in windows:
            ws = window_timestamp(rf.start, w.offset_start_s)
            dur = w.samples.size / sr
            this_gap = gap_before and first
            before_mic = mic_on is not None and ws < mic_on

            do_features = ((not w.is_partial) or process_partial) and dur >= min_window_s
            if do_features and not before_mic:
                # cheap level/clipping first; run the expensive spectral+index stage
                # only when the window is actually non-silent (don't analyse silence).
                feats = compute_features(w.samples, sr, cfg, spectral=False)
                if feats["leq_dbfs_relative"] > cfg["silence_dbfs"]:
                    feats.update(spectral_and_indices(w.samples, sr, cfg))
            else:
                feats = _empty_features()

            flag = qc.classify_window(
                decode_error=False, audio_duration_s=dur, window_s=window_s,
                min_window_s=min_window_s, is_partial=w.is_partial,
                before_mic_on=before_mic, leq_dbfs=feats.get("leq_dbfs_relative"),
                silence_dbfs=cfg["silence_dbfs"], clipped=bool(feats.get("clipped")),
                gap_before=this_gap)

            row = {
                "window_start_timestamp": fmt(ws),
                "window_end_timestamp": fmt(window_timestamp(rf.start, w.offset_end_s)),
                "channel": rf.channel, "source_file": rf.path.name,
                "file_start_timestamp": fmt(rf.start),
                "file_offset_start_s": round(w.offset_start_s, 3),
                "file_offset_end_s": round(w.offset_end_s, 3),
                "window_s": round(dur, 3), "sample_rate_hz": sr,
                "n_samples": int(w.samples.size), "audio_duration_s": round(dur, 3),
                "valid_audio": qc.is_valid(flag), "qc_flag": flag,
            }
            row.update({k: feats.get(k, NAN) for k in COLUMNS if k not in row})
            writer.writerow({c: row.get(c, "") for c in COLUMNS})
            n_rows += 1
            n_valid += int(qc.is_valid(flag))
            first = False
    except DecodeError as e:
        row = _empty_features()
        row.update({
            "window_start_timestamp": fmt(rf.start), "window_end_timestamp": "",
            "channel": rf.channel, "source_file": rf.path.name,
            "file_start_timestamp": fmt(rf.start), "window_s": 0,
            "sample_rate_hz": int(cfg["sample_rate_hz"]), "n_samples": 0,
            "audio_duration_s": 0, "valid_audio": False,
            "qc_flag": qc.DECODE_ERROR, "index_notes": str(e)[:200],
        })
        writer.writerow({c: row.get(c, "") for c in COLUMNS})
        n_rows += 1
    return n_rows, n_valid


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", action="append", help="CH01 / CH02 (repeatable; default: config)")
    ap.add_argument("--date", help="YYYY-MM-DD (filename start date)")
    ap.add_argument("--hours", help="HH-HH within --date, e.g. 12-13")
    ap.add_argument("--start", help="ISO datetime lower bound")
    ap.add_argument("--end", help="ISO datetime upper bound")
    ap.add_argument("--input-root", help="override config input_root")
    ap.add_argument("--output-dir", default=str(PROJECT_ROOT / "outputs"))
    ap.add_argument("--config", default=str(PROJECT_ROOT / "configs" / "audio_analysis.yaml"))
    ap.add_argument("--overwrite", action="store_true", help="reprocess files already in the CSV")
    ap.add_argument("--dry-run", action="store_true", help="list files, decode nothing")
    ap.add_argument("--max-files", type=int, help="cap number of files per channel")
    ap.add_argument("--lightweight", action="store_true", default=True,
                    help="(default) streaming, no plots/WAVs/all-day dataframe")
    args = ap.parse_args()

    cfg = _build_config(args)
    channels = args.channel or cfg["channels"]
    date_filter, start_dt, end_dt = _resolve_window(args, cfg)
    mic_on = datetime.strptime(cfg["mic_on_after"], "%Y-%m-%d %H:%M:%S") if cfg.get("mic_on_after") else None
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for channel in channels:
        files = find_recording_files(cfg["input_root"], channel, date=date_filter,
                                     start=start_dt, end=end_dt, max_files=args.max_files)
        print(f"[{channel}] {len(files)} file(s) match")
        if not files:
            continue

        # group by filename start-date -> one CSV per channel/date
        by_date: dict[str, list] = {}
        for rf in files:
            by_date.setdefault(rf.start.strftime("%Y-%m-%d"), []).append(rf)

        for date_str, group in by_date.items():
            csv_path = out_dir / f"audio_features_{channel}_{date_str}.csv"
            meta_path = out_dir / f"audio_features_{channel}_{date_str}.metadata.json"
            done = set()
            if csv_path.exists() and not args.overwrite:
                try:
                    import pandas as pd
                    done = set(pd.read_csv(csv_path, usecols=["source_file"])["source_file"].unique())
                except Exception:
                    done = set()
            todo = [rf for rf in group if rf.path.name not in done]
            print(f"  {date_str}: {len(group)} file(s), {len(todo)} to process"
                  f"{' (dry-run)' if args.dry_run else ''}")
            if args.dry_run:
                for rf in todo:
                    print(f"    would process {rf.path.name}")
                continue
            if not todo:
                continue

            write_header = args.overwrite or not csv_path.exists()
            mode = "w" if args.overwrite or not csv_path.exists() else "a"
            tot_rows = tot_valid = 0
            prev_end = None
            with csv_path.open(mode, newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=COLUMNS)
                if write_header:
                    writer.writeheader()
                for rf in todo:
                    gap_before = prev_end is not None and \
                        (rf.start - prev_end).total_seconds() > GAP_TOLERANCE_S
                    nr, nv = _process_file(rf, cfg, mic_on, gap_before, writer)
                    fh.flush()
                    tot_rows += nr
                    tot_valid += nv
                    prev_end = rf.end or (rf.start + timedelta(
                        seconds=getattr(rf, "nominal_duration_s", None) or 3600))
                    print(f"    {rf.path.name}: {nr} window(s), {nv} ok")

            meta = {
                "channel": channel, "date": date_str,
                "n_windows_written_this_run": tot_rows, "n_valid_this_run": tot_valid,
                "sample_rate_hz": int(cfg["sample_rate_hz"]),
                "window_s": cfg["window_s"], "subframe_s": cfg["subframe_s"],
                "fft_nperseg": cfg["fft_nperseg"], "fft_noverlap": cfg["fft_noverlap"],
                "bands": cfg["bands"], "anthropophony_band": cfg["anthropophony_band"],
                "biophony_band": cfg["biophony_band"],
                "silence_dbfs": cfg["silence_dbfs"], "clip_amplitude": cfg["clip_amplitude"],
                "mic_on_after": cfg.get("mic_on_after"),
                "level_units": "relative camera-mic dBFS (full-scale ref 1.0); NOT calibrated SPL",
                "timezone_note": cfg.get("timezone_note"),
                "ffmpeg": cfg["ffmpeg"], "library_versions": _lib_versions(),
                "git_commit": _git_commit(),
                "command": "python " + " ".join(sys.argv),
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            meta_path.write_text(json.dumps(meta, indent=2))
            print(f"  wrote {csv_path.name} (+ metadata)")


if __name__ == "__main__":
    main()
