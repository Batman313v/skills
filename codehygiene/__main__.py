"""codehygiene v0.7.0 — PEP 338 CLI entry point (`python -m codehygiene`)."""

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codehygiene import main

raise SystemExit(main())
