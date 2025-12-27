#!/usr/bin/env python3
"""One-off script to initialize the database (create tables).

Usage:
  # Locally (ensure .env is set or env vars present):
  python scripts/init_db.py

  # In Railway (Run One-Off Command):
  python scripts/init_db.py

The script imports the project settings and calls database.db.init_db() and logs errors.
"""
import asyncio
import logging
import os
import sys

# Ensure project root is importable when running from a different cwd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("init_db")

async def main():
    logger.info("Starting database initialization...")
    try:
        await init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.exception(f"Failed to initialize database: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        sys.exit(1)
