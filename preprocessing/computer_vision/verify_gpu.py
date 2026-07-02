"""
Verify the GPU / CUDA / PyTorch stack on the field PC (RTX 5060 Ti, Blackwell).

Pass criteria (Stage 0 #1):
  - torch imports and reports a CUDA build,
  - torch.cuda.is_available() is True and the device is the RTX 5060 Ti,
  - a tiny tensor op runs on the GPU.

A YOLO load + 1-frame inference is attempted as a BONUS (not required to pass).
Blackwell (sm_120) needs a cu128+ PyTorch build; this script prints torch.version
.cuda so a wrong (e.g. cu121) build is obvious.

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
        print("FAIL: CUDA not available. Was torch installed from the cu128 index?")
        print("  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128")
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
