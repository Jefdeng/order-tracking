import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reprocess import build_query, to_epoch
from state import ReadOnlyState, State


def test_to_epoch_respects_time_zone_and_time_of_day():
    la = to_epoch("2026-10-01 09:30", "America/Los_Angeles")
    assert la == int(datetime(2026, 10, 1, 9, 30, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp())
    assert to_epoch("2026-10-01T09:30", "UTC") - la == -7 * 3600    # 09:30 UTC is 7 hours before 09:30 PDT
    assert to_epoch("2026-10-02", "UTC") - to_epoch("2026-10-01", "UTC") == 86400


def test_build_query():
    q = build_query("2026-10-01", "2026-10-02", None, "UTC")
    assert q == "in:inbox after:%d before:%d" % (to_epoch("2026-10-01", "UTC"), to_epoch("2026-10-02", "UTC"))
    assert build_query(None, None, "from:a@b.c", None) == "from:a@b.c"
    assert build_query("2026-10-01", None, "from:a@b.c", "UTC").startswith("from:a@b.c after:")


def test_read_only_state_never_writes(tmp_path):
    inner = State(tmp_path / "s.db")
    ro = ReadOnlyState(inner)
    ro.record("m1", "done"); ro.set("k", "v"); ro.add_pending("p:0", "{}")
    assert inner.message("m1") is None and inner.get("k") is None and inner.list_pending(3600) == []
    inner.record("m2", "done")
    assert ro.message("m2") == ("done", 0)                     # reads pass through
