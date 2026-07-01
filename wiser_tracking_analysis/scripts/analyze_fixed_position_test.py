"""
analyze_fixed_position_test.py
=================================
Main analysis script for the WISER UWB fixed-position test run.

Tags were placed at known fixed locations in the paddock.
The last 10 minutes of each recording are excluded (tags were removed).

Usage:
    python scripts/analyze_fixed_position_test.py
    python scripts/analyze_fixed_position_test.py --data D:/Wiser/data
    python scripts/analyze_fixed_position_test.py --no-plots
    python scripts/analyze_fixed_position_test.py --trim-minutes 5
"""

import argparse
import sys
from pathlib import Path

# Allow running from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.wiser_io   import load_wiser_folder
from src.time_utils import convert_timestamps, trim_last_n_minutes
from src.metrics    import load_ground_truth, compute_summary, add_per_frame_errors
from src.plotting   import generate_all_plots


# ---------------------------------------------------------------------------
# Defaults — change here or override with CLI flags
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR  = Path(r"D:\Wiser\data")
DEFAULT_GT_PATH   = PROJECT_ROOT / "configs" / "fixed_position_ground_truth.csv"
DEFAULT_OUT_DIR   = PROJECT_ROOT / "outputs"
DEFAULT_TRIM_MIN  = 10.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse WISER UWB fixed-position test data."
    )
    parser.add_argument("--data",         type=Path, default=DEFAULT_DATA_DIR,
                        help="Folder containing raw WISER files.")
    parser.add_argument("--gt",           type=Path, default=DEFAULT_GT_PATH,
                        help="Ground-truth CSV path.")
    parser.add_argument("--output",       type=Path, default=DEFAULT_OUT_DIR,
                        help="Output folder for CSVs and plots.")
    parser.add_argument("--trim-minutes", type=float, default=DEFAULT_TRIM_MIN,
                        help="Minutes to trim from end of recording.")
    parser.add_argument("--no-plots",     action="store_true",
                        help="Skip generating plots.")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    print("\n=== 1. Loading WISER data ===")
    df = load_wiser_folder(args.data)

    # ------------------------------------------------------------------
    # 2. Convert timestamps
    # ------------------------------------------------------------------
    print("\n=== 2. Converting timestamps ===")
    df = convert_timestamps(df, raw_col="ts_raw")

    # ------------------------------------------------------------------
    # 3. Trim last N minutes
    # ------------------------------------------------------------------
    print(f"\n=== 3. Trimming last {args.trim_minutes} minutes ===")
    df = trim_last_n_minutes(df, minutes=args.trim_minutes)

    print(f"  Rows after trim: {len(df):,}")
    print(f"  Tags present: {sorted(df['shortid'].unique())}")

    # ------------------------------------------------------------------
    # 4. Load ground truth (optional)
    # ------------------------------------------------------------------
    print("\n=== 4. Loading ground truth ===")
    gt = load_ground_truth(args.gt)

    # ------------------------------------------------------------------
    # 5. Per-frame error / jitter columns
    # ------------------------------------------------------------------
    print("\n=== 5. Computing per-frame metrics ===")
    df = add_per_frame_errors(df, ground_truth=gt)

    # ------------------------------------------------------------------
    # 6. Summary table
    # ------------------------------------------------------------------
    print("\n=== 6. Computing per-tag summary ===")
    summary = compute_summary(df, ground_truth=gt)
    print(summary.to_string())

    # ------------------------------------------------------------------
    # 7. Save outputs
    # ------------------------------------------------------------------
    print("\n=== 7. Saving outputs ===")

    cleaned_path = args.output / "fixed_position_cleaned.csv"
    df.to_csv(cleaned_path, index=False)
    print(f"  Cleaned data -> {cleaned_path}")

    summary_path = args.output / "fixed_position_summary.csv"
    summary.to_csv(summary_path)
    print(f"  Summary table -> {summary_path}")

    # ------------------------------------------------------------------
    # 8. Plots
    # ------------------------------------------------------------------
    if not args.no_plots:
        print("\n=== 8. Generating plots ===")
        plots_dir = args.output / "plots"
        generate_all_plots(df, ground_truth=gt, output_dir=plots_dir)

    print("\n=== Done ===")
    print(f"Results written to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
