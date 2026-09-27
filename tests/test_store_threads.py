"""The store's one connection is shared by the serve loop, the collector and the HTTP handlers: use from other threads."""
import threading

import pandas as pd

from mta_delay_insights.storage.db import Store

T0 = 1_790_000_000.0


def test_store_is_usable_from_other_threads(tmp_path):
    store = Store(tmp_path / "t.sqlite")        # opened on this thread, like cmd_serve does
    errors: list[BaseException] = []

    def writer(k: int):
        try:
            for i in range(20):
                store.insert_snapshot(f"feed{k}", T0 + 30 * i, T0 + 30 * i - 5, 10, 5, 3)
            store.put_frame(f"ctx{k}", pd.DataFrame({"k": [k], "v": [float(k)]}))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    def reader():
        try:
            for _ in range(20):
                store.snapshot_stats(); store.coverage_intervals(); store.arrivals(None, T0, T0 + 3600)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)] + [threading.Thread(target=reader) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)
    assert not errors, errors
    stats = store.snapshot_stats().set_index("feed")["polls"]
    assert sorted(stats.index) == ["feed0", "feed1", "feed2", "feed3"] and (stats == 20).all()
    assert store.get_frame("ctx2").iloc[0]["v"] == 2.0
    store.close()
