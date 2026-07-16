#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.config import load_config  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in {"server", "setups"}:
        print(f"usage: {sys.argv[0]} CONFIG_PATH server|setups", file=sys.stderr)
        return 2

    config = load_config(sys.argv[1])
    if sys.argv[2] == "server":
        print(config.host)
        print(config.port)
    else:
        print(config.ros_setup)
        for setup in config.monitor_setups:
            print(setup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
