from dataclasses import dataclass

import psycopg
import redis


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def check_postgres(url: str) -> Check:
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            row = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchone()
        tables = row[0] if row else 0
        return Check("postgres", True, f"{tables} tables")
    except Exception as exc:
        return Check("postgres", False, type(exc).__name__)


def check_redis(url: str) -> Check:
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=3)
        client.ping()
        return Check("redis", True)
    except Exception as exc:
        return Check("redis", False, type(exc).__name__)