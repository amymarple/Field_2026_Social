"""Lightweight environmental-audio feature pipeline (Field_2026_Social).

Extracts RELATIVE camera-mic level + band-limited soundscape indices from Reolink
hourly MP4 audio into compact, timestamped CSVs. NOT calibrated SPL. See README.
"""
# Make native DLLs (libsndfile, MKL, etc.) loadable when this env's python.exe is
# invoked DIRECTLY (without `conda activate`). Without this, importing soundfile/
# librosa/maad hard-crashes the process (0xC06D007F) because the env's Library\bin
# is not on the DLL search path. Safe no-op off-Windows / if the dir is missing.
import os as _os
import sys as _sys
from pathlib import Path as _Path

if _sys.platform == "win32":
    _extra = []
    for _sub in ("Library/bin", "Library/mingw-w64/bin", "DLLs"):
        _d = _Path(_sys.prefix) / _sub
        if _d.is_dir():
            _extra.append(str(_d))
            try:
                _os.add_dll_directory(str(_d))
            except Exception:
                pass
    # cffi/ctypes (libsndfile) resolve via PATH, not add_dll_directory -> prepend too.
    if _extra:
        _os.environ["PATH"] = _os.pathsep.join(_extra + [_os.environ.get("PATH", "")])
