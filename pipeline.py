"""Orchestration: new Gmail messages -> LLM extraction -> match to sheet rows -> update.

process_new_mail() is the single entry point, used by the local poller, the Pub/Sub webhook,
the scheduled backup poll, and `python pipeline.py`. It's serialised with a lock and idempotent
per Gmail message ID.
"""
import json
import logging
import threading
import time
from typing import Dict, Iterable, List, Optional

from google.genai import errors as genai_errors

from config import settings
from extractor import Extractor
from gmail_client import GmailClient, HistoryExpired, InboundEmail
from matching import match_update
from models import OrderUpdate
from sheets import ATTENTION_EVENTS, EVENT_LABEL, SheetClient, plan_update, tracked_rows
from state import get_state

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6  # with 60s*attempt backoff: gives up after ~15 min of failures
PENDING_RETRY_SECONDS = 60
_lock = threading.Lock()


class _Services:
    """Builds the sheet/LLM clients lazily so a run with no new mail makes no Sheets/LLM calls."""

    def __init__(self) -> None:
        self._sheet: Optional[SheetClient] = None
        self._extractor: Optional[Extractor] = None

    @property
    def sheet(self) -> SheetClient:
        if self._sheet is None:
            self._sheet = SheetClient()
        return self._sheet

    @property
    def extractor(self) -> Extractor:
        if self._extractor is None:
            self._extractor = Extractor()
        return self._extractor


def _prefix() -> str:
    return "[DRY RUN] " if settings.dry_run else ""


def apply_update(email: InboundEmail, upd: OrderUpdate, index: int, services: _Services, state) -> str:
    """Match one extracted update to sheet rows and apply it. Returns 'applied' | 'noop' | 'review'."""
    rows = services.sheet.read()
    m = match_update(rows, upd)
    key = services.sheet.review.key(email, index)

    if not m.rows:
        logger.info("%sNeeds review (%s): %s | order %s", _prefix(), m.reason, email.subject, upd.order_number)
        if not settings.dry_run:
            services.sheet.review.add(email, upd, index, "No row updated: " + m.reason)
            # The Order #/SKU may simply not be on the sheet yet; retry for a few days.
            state.add_pending(key, json.dumps({
                "email": {"id": email.id, "thread_id": email.thread_id, "sender": email.sender,
                          "subject": email.subject, "date_iso": email.date_iso},
                "update": upd.model_dump()}))
        return "review"

    scope = "applies to all %d items on the order" % len(m.rows) if m.whole_order else ""
    plans = [plan_update(rows, n, upd, email.date_iso, fill_order_no=(m.how == "sku"), scope_note=scope)
             for n in m.rows]
    for p in plans:
        logger.info("%s%s", _prefix(), p.summary)
        if settings.dry_run and p.values:
            logger.info("[DRY RUN]   values: %s", p.values)
        if not settings.dry_run:
            services.sheet.apply(rows, p)
    if not settings.dry_run:
        services.sheet.log.add(email, upd, plans)
        if upd.event in ATTENTION_EVENTS:
            services.sheet.review.add(email, upd, index, "Attention: order %s is %s" % (
                upd.order_number or "?", EVENT_LABEL[upd.event].lower()), "; ".join(p.summary for p in plans))
    return "applied" if any(p.action == "update" for p in plans) else "noop"


def process_email(email: InboundEmail, services: _Services, state) -> str:
    """Extract one email and apply it to the sheet. Returns the status recorded."""
    rows = services.sheet.read()
    result = services.extractor.extract(email, tracked_rows(rows))
    if not result.is_order_related or not result.updates:
        logger.info("Skipped (not an order update): %s | %s", email.sender, email.subject)
        state.record(email.id, "skipped", email.subject)
        return "skipped"

    logger.info("Order email: %s | %s -> %d update(s)", email.sender, email.subject, len(result.updates))
    for i, upd in enumerate(result.updates):
        apply_update(email, upd, i, services, state)
    status = "dry_run" if settings.dry_run else "done"
    state.record(email.id, status, email.subject)
    return status


def retry_pending(services: _Services, state) -> int:
    """Re-try updates that matched no row (e.g. the Order # was added to the sheet after the email arrived)."""
    pending = state.list_pending(settings.pending_days * 86400)
    if not pending:
        return 0
    rows = services.sheet.read()
    resolved = 0
    for key, payload in pending:
        data = json.loads(payload)
        upd = OrderUpdate.model_validate(data["update"])
        m = match_update(rows, upd)
        if not m.rows:
            continue
        e = data["email"]
        email = InboundEmail(id=e["id"], thread_id=e["thread_id"], sender=e["sender"], subject=e["subject"],
                             date_iso=e["date_iso"], body="")
        logger.info("Pending update now matches the sheet: %s | order %s", email.subject, upd.order_number)
        apply_update(email, upd, int(key.rsplit(":", 1)[1]), services, state)
        services.sheet.review.mark_resolved(key, "auto-applied")
        state.remove_pending(key)
        rows = services.sheet.read()
        resolved += 1
    return resolved


def _is_quota_error(e: Exception) -> bool:
    return isinstance(e, genai_errors.APIError) and e.code == 429


def process_message_ids(ids: Iterable[str], gmail: GmailClient, services: _Services, state) -> Dict[str, int]:
    ids = list(ids)
    counts: Dict[str, int] = {}
    for pos, mid in enumerate(ids):
        try:
            status = process_email(gmail.get_message(mid), services, state)
        except Exception as e:  # keep going; failed messages are retried on later runs
            if _is_quota_error(e):
                # Daily/minute quota: stop hammering the API and hold this and every remaining message.
                logger.error("Gemini quota exhausted (HTTP 429). Holding %d message(s) and retrying in ~5 min. "
                             "Enable billing on the API key's project to lift the free-tier limit.", len(ids) - pos)
                for held in ids[pos:]:
                    state.record(held, "deferred", "quota exhausted")
                counts["deferred"] = counts.get("deferred", 0) + len(ids) - pos
                return counts
            logger.exception("Failed processing message %s", mid)
            state.record(mid, "failed", repr(e))
            status = "failed"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _run() -> Dict[str, int]:
    state = get_state()
    gmail = GmailClient()
    services = _Services()

    history_id = state.get("history_id")
    if history_id is None:
        history_id = gmail.profile_history_id()
        state.set("history_id", history_id)
        logger.info("First run: baseline historyId=%s. Only mail arriving from now on is processed.", history_id)
        return {}

    try:
        new_ids, newest = gmail.new_message_ids(history_id)
    except HistoryExpired:
        logger.warning("Stored historyId %s expired; falling back to last day of inbox mail", history_id)
        new_ids = gmail.search_message_ids("in:inbox newer_than:1d", limit=50)
        newest = gmail.profile_history_id()

    # Never-seen messages, plus earlier failures whose backoff has elapsed.
    todo: List[str] = [m for m in new_ids if state.message(m) is None]
    todo += [m for m in state.retryable_failures(MAX_ATTEMPTS) if m not in todo]

    counts = process_message_ids(todo, gmail, services, state) if todo else {}
    state.set("history_id", newest)

    last = float(state.get("pending_checked") or 0)
    if not settings.dry_run and time.time() - last >= PENDING_RETRY_SECONDS:
        state.set("pending_checked", str(time.time()))
        try:
            n = retry_pending(services, state)
            if n:
                counts["pending_resolved"] = n
        except Exception:
            logger.exception("Retrying pending updates failed")
    if counts:
        logger.info("Run complete: %s", counts)
    return counts


def process_new_mail() -> Dict[str, int]:
    with _lock:
        return _run()


def reprocess(query: str, limit: int, force: bool) -> Dict[str, int]:
    """Run extraction on messages matching a Gmail search, ignoring the history cursor."""
    with _lock:
        state = get_state()
        gmail = GmailClient()
        ids = gmail.search_message_ids(query, limit)
        if not force:
            ids = [i for i in ids if state.message(i) is None or state.message(i)[0] == "failed"]
        logger.info("Reprocessing %d message(s) for query %r", len(ids), query)
        return process_message_ids(ids, gmail, _Services(), state)


def ensure_watch() -> None:
    """(Re)register the Gmail -> Pub/Sub watch. Must run at least every 7 days."""
    if not settings.pubsub_topic:
        raise RuntimeError("PUBSUB_TOPIC is not set")
    resp = GmailClient().start_watch(settings.pubsub_topic)
    state = get_state()
    if state.get("history_id") is None:
        state.set("history_id", str(resp["historyId"]))
    logger.info("Gmail watch active, expires at epoch-ms %s", resp.get("expiration"))


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(process_new_mail())
