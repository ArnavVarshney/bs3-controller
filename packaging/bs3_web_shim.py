"""PyInstaller entry shim: bs3-web dashboard + JSON API (no source checkout needed)."""
from bs3.webapp import main

if __name__ == "__main__":
    raise SystemExit(main())
