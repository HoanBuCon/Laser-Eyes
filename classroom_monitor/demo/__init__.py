"""VIGIL AI SRS v2.0 - Demo & Prototype Execution Package.

Provides a unified, modular architecture for real classroom video evaluation:
- config: Demo configuration definitions, presets, and argument parsing.
- renderer: HUD overlay rendering with optional real-time debug telemetry.
- exporter: Standardized export of canonical episodes, events, summaries, and profiles.
- runner: End-to-end video pipeline orchestrator.
"""

from classroom_monitor.demo.config import (
    DEMO_PRESETS,
    DemoVideoConfig,
    build_arg_parser,
    get_demo_config,
    resolve_video_path,
)
from classroom_monitor.demo.exporter import export_demo_artifacts
from classroom_monitor.demo.renderer import DemoHUDOverlayRenderer
from classroom_monitor.demo.runner import (
    run_classroom_demo,
    run_demo_pipeline,
    setup_database_seats,
    setup_room_seats,
)

__all__ = [
    "DEMO_PRESETS",
    "DemoVideoConfig",
    "build_arg_parser",
    "get_demo_config",
    "resolve_video_path",
    "export_demo_artifacts",
    "DemoHUDOverlayRenderer",
    "run_demo_pipeline",
    "run_classroom_demo",
    "setup_room_seats",
    "setup_database_seats",
]
