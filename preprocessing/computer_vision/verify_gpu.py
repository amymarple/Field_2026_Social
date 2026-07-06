"""
Verify the GPU / CUDA / PyTorch stack.

Pass criteria (Stage 0 #1):
  - torch imports and reports a CUDA build,
  - torch.cuda.is_available() is True,
  - a tiny tensor op runs on whatever CUDA device is present.

Works on either machine: the field PC (RTX 5060 Ti, Blackwell sm_120, needs a cu128+
build) or the analysis PC (RTX 3060, Ampere sm_86, a standard cu12x build). The device
name/capability is printed, not asserted, so any CUDA GPU passes. torch.version.cuda is
printed so a wrong build for the installed GPU is obvious.

A YOLO load + 1-frame inference is attempted as a BONUS (not required to pass).

Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import torch
    except Exception as e:
        print(f"FAIL: cannot import torch: {e}")
        return 1

    print(f"torch            : {torch.__version__}")
    print(f"torch CUDA build : {torch.version.cuda}")
    avail = torch.cuda.is_available()
    print(f"cuda available   : {avail}")
    if not avail:
        print("FAIL: CUDA not available. Was torch installed from a CUDA index matching this GPU?")
        print("  Blackwell RTX 5060 Ti (sm_120): --index-url https://download.pytorch.org/whl/cu128")
        print("  Ampere    RTX 3060   (sm_86) : --index-url https://download.pytorch.org/whl/cu124")
        return 1

    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    print(f"device           : {name}")
    print(f"capability       : sm_{cap[0]}{cap[1]}")
    try:
        x = torch.rand(2048, 2048, device="cuda")
        y = (x @ x).sum().item()
        print(f"gpu matmul ok    : sum={y:.3e}")
    except Exception as e:
        print(f"FAIL: GPU op failed (likely a torch build without sm_{cap[0]}{cap[1]}): {e}")
        return 1

    # bonus: YOLO
    try:
        from ultralytics import YOLO
        import numpy as np
        m = YOLO("yolo11n.pt")
        _ = m.predict(np.zeros((640, 640, 3), dtype="uint8"), device=0, verbose=False)
        print("ultralytics YOLO : load + inference OK (bonus)")
    except Exception as e:
        print(f"ultralytics YOLO : skipped/failed (bonus, not required): {e}")

    print("\nPASS: GPU stack is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
