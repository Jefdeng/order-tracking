import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state import State


def test_failed_messages_back_off_and_stop_after_max_attempts(tmp_path):
    st = State(tmp_path / "s.db")
    st.record("m1", "failed", "503")
    assert st.retryable_failures(6, backoff_seconds=60) == []            # just failed: wait
    assert st.retryable_failures(6, backoff_seconds=0) == ["m1"]         # backoff elapsed
    for _ in range(5):
        st.record("m1", "failed", "503")
    assert st.message("m1") == ("failed", 6)
    assert st.retryable_failures(6, backoff_seconds=0) == []             # attempts exhausted
    st.record("m2", "done")
    assert st.retryable_failures(6, backoff_seconds=0) == []


def test_deferred_messages_wait_then_retry_without_using_attempts(tmp_path):
    st = State(tmp_path / "s.db")
    st.record("q1", "deferred", "quota")
    assert st.retryable_failures(6, defer_seconds=300) == []             # held for now
    assert st.retryable_failures(6, defer_seconds=0) == ["q1"]           # due
    for _ in range(10):
        st.record("q1", "deferred", "quota")
    assert st.message("q1") == ("deferred", 0)                           # never exhausts
    assert st.retryable_failures(6, defer_seconds=0) == ["q1"]


def test_pending_updates_roundtrip_and_expire(tmp_path):
    st = State(tmp_path / "s.db")
    st.add_pending("m1:0", '{"a": 1}')
    st.add_pending("m1:0", '{"a": 2}')                         # duplicate key ignored
    assert st.list_pending(3600) == [("m1:0", '{"a": 1}')]
    st.remove_pending("m1:0")
    assert st.list_pending(3600) == []
    st.add_pending("old:0", "{}")
    time.sleep(0.05)
    assert st.list_pending(0.01) == []                         # older than the window: dropped
