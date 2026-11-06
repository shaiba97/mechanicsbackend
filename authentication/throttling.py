import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def allow_burst(key: str, limit: int, window_seconds: float = 60.0) -> bool:
    now = time.monotonic()
    with _lock:
        stamps = [t for t in _hits[key] if t > now - window_seconds]
        if len(stamps) >= limit:
            _hits[key] = stamps
            return False
        stamps.append(now)
        _hits[key] = stamps
        if len(_hits) > 5000:
            cutoff = now - window_seconds
            for stale_key in [k for k, v in _hits.items() if all(t <= cutoff for t in v)]:
                _hits.pop(stale_key, None)
        return True


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    addr = request.META.get("REMOTE_ADDR", "unknown")
    return addr or "unknown"
