"""
analyze_formal_recording.py
==============================
Placeholder script for future formal WISER field recordings.

When real tracking sessions are acquired (animals moving freely), run this
script instead of analyze_fixed_position_test.py.

Key differences from the fixed-position test:
  - No end-trim (tags are not removed; the full recording is valid).
  - No ground truth (animals move, so absolute error cannot be computed).
  - Output focuses on trajectory visualisation and data quality checks.

Usage (once data is available):
    python scripts/analyze_formal_recording.py --data D:/Wiser/field_data/session1

TODO: implement trajectory smoothing, gap detection, and session-level QC.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.wiser_io   import load_wiser_folder
from src.time_utils import convert_timestamps


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse a formal WISER field recording session."
    )
    parser.add_argument("--data",   type=Path, required=True,
                        help="Folder containing raw WISER files for this session.")
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "outputs" / "formal",
                        help="Output folder for results.")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    print("\n=== Loading WISER field data ===")
    df = load_wiser_folder(args.data)

    print("\n=== Converting timestamps ===")
    df = convert_timestamps(df, raw_col="ts_raw")

    print(f"\nLoaded {len(df):,} rows | "
          f"{df['shortid'].nunique()} tag(s) | "
          f"Duration: {df['elapsed_s'].max() / 60:.1f} min")

    # Save cleaned data.
    out_path = args.output / "formal_recording_cleaned.csv"
    df.to_csv(out_path, index=False)
    print(f"\nCleaned data saved to: {out_path}")

    print("\n[Placeholder] Trajectory analysis and QC not yet implemented.")
    print("Edit this script to add per-session analysis as needed.")


if __name__ == "__main__":
    main()
