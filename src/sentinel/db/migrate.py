"""Apply database migrations: every db/migrations/*.sql file not applied yet, in name order.

python -m sentinel.db.migrate
"""

from pathlib import Path

import psycopg

from sentinel.settings import get_settings

MIGRATIONS_DIR = Path("db") / "migrations"


def migrate(database_url: str | None = None, folder: Path = MIGRATIONS_DIR) -> list[str]:
    """Returns the names of the migrations it applied (empty if the database was up to date)."""
    url = database_url or get_settings().database_url
    applied_now = []
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name       TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(folder.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    names = migrate()
    print("applied: " + ", ".join(names) if names else "database is up to date")
