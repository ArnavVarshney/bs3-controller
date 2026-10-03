"""PyInstaller entry shim: bs3ctl command line."""
from bs3.controller_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
