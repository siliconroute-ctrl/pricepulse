"""A busy database makes visitors wait instead of failing with 'database is locked'."""

import threading
import time

from sqlalchemy import text

from pricepulse.api import ingest_routes
from pricepulse.db import BUSY_SECONDS, make_engine


def test_writers_wait_for_each_other(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'busy.db'}")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE t (x INTEGER)"))
        assert c.execute(text("PRAGMA busy_timeout")).scalar() >= 30000
    assert BUSY_SECONDS >= 30

    started = threading.Event()

    def long_writer():
        with engine.begin() as c:
            c.execute(text("INSERT INTO t VALUES (1)"))
            started.set()
            time.sleep(1.5)  # a big upload holding the write lock

    th = threading.Thread(target=long_writer)
    th.start()
    started.wait()
    with engine.begin() as c:  # a visitor's write: waits, then succeeds
        c.execute(text("INSERT INTO t VALUES (2)"))
    th.join()
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM t")).scalar() == 2


def test_the_matching_pass_runs_once_per_gap():
    ingest_routes._rematch_last = 0.0
    assert ingest_routes._rematch_due(600) is True  # the first upload of the night matches
    assert ingest_routes._rematch_due(600) is False  # the other eight province files do not
    assert ingest_routes._rematch_due(0) is True  # a zero gap always matches (tests)
