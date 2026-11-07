"""Entry point of TrackXen.exe (PyInstaller): see trackxen.desktop."""

import sys

from trackxen.desktop import main

if __name__ == "__main__":
    sys.exit(main())
