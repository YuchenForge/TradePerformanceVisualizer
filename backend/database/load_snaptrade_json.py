"""Compatibility entry point for the shared, transactional database importer."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from app import app
    from database.db_sync import sync_export_to_db
    with app.app_context():
        sync_export_to_db(verbose=True)
