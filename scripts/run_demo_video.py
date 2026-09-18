#!/usr/bin/env python
"""Runner script for VIGIL AI SRS v2.0 Demo Video Processing.

Alias and CLI entry point for processing videos in `video/` or `demo_video/`.
"""

import sys
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_demo_all_videos import main

if __name__ == "__main__":
    main()
