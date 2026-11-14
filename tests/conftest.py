"""Shared setup for the tests: use a throwaway database and make the project importable."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["HOSPITAL_DB"] = str(Path(tempfile.mkdtemp()) / "test_hospital.db")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hospital"))
