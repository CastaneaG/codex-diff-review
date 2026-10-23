#!/usr/bin/env python3
"""Plugin-relative launcher; works from any project without installation."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'src'))
from diff_review.cli import main

if __name__=='__main__':
    raise SystemExit(main())
