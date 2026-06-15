#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from model_control_plane import app as app_module  # noqa: E402
from model_control_plane import cron_tick as cron_tick_planner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create cron-friendly Model Plane agent job packets.")
    parser.add_argument(
        "--health-stale-seconds",
        type=int,
        default=cron_tick_planner.DEFAULT_HEALTH_STALE_SECONDS,
        help="Age after which a healthy run should receive a health-check review packet.",
    )
    args = parser.parse_args()
    result = app_module.run_cron_tick(app_module.CronTickRequest(health_stale_seconds=args.health_stale_seconds))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
