"""Download the face models for Registered Face Tracking into models/ (once per PC).

  python tools/get_face_models.py

From OpenCV's model zoo (github.com/opencv/opencv_zoo), Apache-2.0 / MIT licensed:
- YuNet face detector (finds faces and their 5 landmarks), ~0.2 MB
- SFace face recognizer (a 128-number fingerprint of a face), ~37 MB
Prints each file's size and SHA-256 so the download can be checked.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import httpx  # verifies certificates with certifi's CA list (Python's own store can miss GitHub's issuer)

MODELS = Path(__file__).resolve().parent.parent / "models"
BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"
FILES = {
    "face_detection_yunet_2023mar.onnx": f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def main() -> int:
    MODELS.mkdir(exist_ok=True)
    for name, url in FILES.items():
        path = MODELS / name
        if not path.exists():
            print(f"downloading {name} ...", flush=True)
            tmp = path.with_suffix(".part")
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as reply:
                reply.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in reply.iter_bytes():
                        f.write(chunk)
            data = tmp.read_bytes()
            if data[:20].lstrip().startswith(b"version https://git-lfs"):  # a pointer file, not the model
                tmp.unlink()
                print(f"  {name}: got a Git LFS pointer instead of the model; download it by hand from {url}")
                return 1
            tmp.replace(path)
        data = path.read_bytes()
        print(f"{name}: {len(data) / 1e6:.2f} MB, sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
