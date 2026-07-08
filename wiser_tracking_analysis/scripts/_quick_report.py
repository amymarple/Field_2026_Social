import sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.wiser_io   import load_wiser_folder
from src.time_utils import convert_timestamps, trim_last_n_minutes
from src.metrics    import load_ground_truth, compute_summary, add_per_frame_errors
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

print("Loading data...")
df = load_wiser_folder(r"D:\Wiser\data")
df = convert_timestamps(df)
df_trimmed = trim_last_n_minutes(df, minutes=10)

# --- Sampling frequency per tag ---
print("\n=== Sampling frequency per tag (last 10 min excluded) ===")
rows = []
for tag, g in df_trimmed.groupby("shortid"):
    g = g.sort_values("datetime")
    duration_s = (g["datetime"].max() - g["datetime"].min()).total_seconds()
    n = len(g)
    hz = n / duration_s if duration_s > 0 else float("nan")
    diffs = g["elapsed_s"].diff().dropna()
    rows.append({
        "shortid":           tag,
        "n_frames":          n,
        "duration_min":      round(duration_s / 60, 1),
        "mean_hz":           round(hz, 3),
        "median_interval_s": round(diffs.median(), 4),
    })
freq_df = pd.DataFrame(rows).set_index("shortid")
print(freq_df.to_string())

# --- Jitter + error ---
gt = load_ground_truth(ROOT / "configs" / "fixed_position_ground_truth.csv")
df_trimmed = add_per_frame_errors(df_trimmed, ground_truth=gt)
summary = compute_summary(df_trimmed, ground_truth=gt)

print("\n=== Jitter / precision (metres) ===")
jitter_cols = ["n_frames", "std_x", "std_y", "rms_jitter",
               "jitter_p50", "jitter_p75", "jitter_p90", "jitter_p95"]
print(summary[jitter_cols].round(3).to_string())

error_cols = ["true_x", "true_y", "bias_x", "bias_y", "bias_mag",
              "mean_error", "rmse", "error_p50", "error_p90", "error_p95"]
present = [c for c in error_cols if c in summary.columns]
if present:
    sub = summary[present].dropna(subset=["true_x"])
    if not sub.empty:
        print("\n=== Accuracy vs ground truth (metres) ===")
        print(sub.round(3).to_string())
