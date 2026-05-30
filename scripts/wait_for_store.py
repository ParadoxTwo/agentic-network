"""Block until Postgres and Redis accept connections (bounded retry).

Used by `make dev` between starting the services and applying the schema, so
a not-yet-ready database doesn't cause a spurious failure. Exits 0 when both
are reachable, 1 if either stays down.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

from store import connect_pg, connect_redis


def _wait(name: str, probe: Callable[[], object], attempts: int = 30, delay: float = 1.0) -> bool:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            probe()
            print(f"{name}: ready")
            return True
        except Exception as exc:  # noqa: BLE001 - retry any connection failure
            last = exc
            time.sleep(delay)
    print(f"{name}: NOT ready after {attempts}s: {last}", file=sys.stderr)
    return False


def main() -> int:
    pg_ok = _wait("postgres", lambda: connect_pg().close())
    redis_ok = _wait("redis", lambda: connect_redis().ping())
    return 0 if (pg_ok and redis_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
