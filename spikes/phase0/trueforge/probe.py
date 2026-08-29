from __future__ import annotations

import json

from .runner import run_live_probe


def main() -> None:
    try:
        result = run_live_probe()
    except Exception:
        print(json.dumps({"overall": "BLOCKED_HARD_GATE"}, separators=(",", ":")))
        raise SystemExit(1)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
