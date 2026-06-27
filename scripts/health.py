from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.sqlite import query_sessions

BASE_URL = "http://localhost:8000"
HEALTH_URL = f"{BASE_URL}/health"
ACTORS_URL = f"{BASE_URL}/actors"


def _get_json(url: str, timeout: int = 5) -> tuple[int, object]:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        status = resp.status
        body = resp.read().decode()
        return status, json.loads(body)


def check_health(url: str = HEALTH_URL) -> int:
    try:
        status, body = _get_json(url)
        print(f"health: {status} {body}")
        return 0 if status == 200 else 1
    except URLError as exc:
        print(f"health check failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"health check error: {exc}", file=sys.stderr)
        return 1


def print_actors(url: str = ACTORS_URL) -> int:
    try:
        _, actors = _get_json(url)
    except Exception as exc:
        print(f"actors check failed: {exc}", file=sys.stderr)
        return 1
    if not actors:
        print("no actors")
        return 0
    print(f"{'name':16} {'state':10} {'alive':6} {'paused':7}")
    for a in actors:
        print(f"{a['name']!s:16} {a['state']!s:10} {a['alive']!s:6} {a['paused']!s:7}")
    return 0


def print_sessions() -> None:
    import asyncio

    sessions = asyncio.run(query_sessions())
    if not sessions:
        print("no sessions")
        return
    print(f"{'session_id':36} {'actor':12} {'website':24} {'status':8} {'errors':6}")
    for s in sessions:
        print(
            f"{s['id']:36} {s['actor_id']!s:12} {s['website']!s:24} "
            f"{s['status']!s:8} {s['errors']:6}"
        )


def main() -> int:
    rc = check_health()
    if rc == 0:
        rc |= print_actors()
    print_sessions()
    return rc


if __name__ == "__main__":
    sys.exit(main())
