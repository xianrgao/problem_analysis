"""Portable entry point; implementation lives in the line_engineer package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from line_engineer.cli import main

if __name__ == "__main__":
    main()
