"""Pytest configuration and global fixtures."""

import sys
from pathlib import Path

# Ensure 'src' is in python path
src_dir = Path(__file__).resolve().parents[1] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
