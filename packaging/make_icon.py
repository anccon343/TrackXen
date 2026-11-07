"""Draw TrackXen's icon (packaging/trackxen.ico): the red triangle (trackxen/brand.py), at every size Windows uses."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from trackxen.brand import mark  # noqa: E402

if __name__ == "__main__":
    out = Path(__file__).with_name("trackxen.ico")
    mark(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
