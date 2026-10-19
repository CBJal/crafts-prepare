"""Frozen executable entry. Development web UI still uses `python -m app`."""

from app.desktop.main import main

if __name__ == "__main__":
    main()
