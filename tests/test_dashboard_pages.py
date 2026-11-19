"""Render each dashboard page once against the real database, to catch errors before a browser does."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sentinel.health import check_postgres
from sentinel.settings import get_settings

PAGES = sorted(Path("dashboard/pages").glob("*.py"))


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_renders_without_errors(page: Path) -> None:
    if not check_postgres(get_settings().database_url).ok:
        pytest.skip("Postgres is not running (start it with: docker compose up -d)")
    at = AppTest.from_file(str(page.resolve()), default_timeout=30).run()
    assert not at.exception, [e.message for e in at.exception]
