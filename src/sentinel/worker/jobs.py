import time
from datetime import UTC, datetime


def slow_ping(message: str, seconds: int = 5) -> dict[str, str]:
    """A stand-in for slow work, like an LLM call. Waits, then answers."""
    time.sleep(seconds)
    return {"message": message, "finished_at": datetime.now(UTC).isoformat()}