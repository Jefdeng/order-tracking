import os
import sys

import httplib2
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import process_message_ids
from state import State


class _GoneGmail:
    """Every message 404s, as when an email is deleted before the poll fetches it."""

    def __init__(self, status: int = 404) -> None:
        self.status = status

    def get_message(self, msg_id):
        raise HttpError(httplib2.Response({"status": self.status}), b"Requested entity was not found.")


def test_deleted_message_is_skipped_not_retried(tmp_path):
    st = State(tmp_path / "s.db")
    counts = process_message_ids(["m1"], _GoneGmail(), services=None, state=st)
    assert counts == {"skipped": 1}
    assert st.message("m1") == ("skipped", 0)
    assert st.retryable_failures(6, backoff_seconds=0) == []


def test_other_gmail_errors_are_still_retried(tmp_path):
    st = State(tmp_path / "s.db")
    counts = process_message_ids(["m1"], _GoneGmail(status=500), services=None, state=st)
    assert counts == {"failed": 1}
    assert st.retryable_failures(6, backoff_seconds=0) == ["m1"]
