"""Root conftest — sets DATABASE_URL before any app module is imported."""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
