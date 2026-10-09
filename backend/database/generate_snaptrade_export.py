"""Compatibility entry point. Use --user USERNAME to export one stored user."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.snaptrade_export_all import main

if __name__ == "__main__":
    main()
