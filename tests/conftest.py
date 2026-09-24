from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from mta_delay_insights import synthetic
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS

START = date(2026, 8, 24)   # Monday
END = date(2026, 9, 21)


@pytest.fixture(scope="session")
def mini_gtfs_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("gtfs")
    return synthetic.build_mini_gtfs(d, START - timedelta(days=1), END + timedelta(days=1))


@pytest.fixture(scope="session")
def static(mini_gtfs_dir) -> StaticGTFS:
    return StaticGTFS.load(mini_gtfs_dir)


def dt(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), NY_TZ)
