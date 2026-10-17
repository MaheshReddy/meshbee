#!/usr/bin/env python3
"""Checkout convenience shim — `python3 server.py` keeps working.

The real server lives in meshbee/server.py (installable package; see README).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from meshbee.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
