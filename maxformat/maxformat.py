#!/usr/bin/env python3
"""Make Max patches clean and readable.

    python3 maxformat.py input.maxpat output.maxpat
    python3 maxformat.py --help
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from maxlayout.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
