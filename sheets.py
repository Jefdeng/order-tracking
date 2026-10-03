"""Google Sheet logic: decide what to change (pure) and apply it via gspread.

The tab has title rows above the table, so the header row is located by finding the row that
contains both "Room" and "Item". Header names are normalised and aliased
(e.g. "Order #" -> order_no, "Estimate Arrival" -> est_arrival).

Scope: post-order tracking. A vendor email updates rows that already carry its Order # / SKU
(see matching.py). Rows are never created. For a matched row:
  status           advance only along STATUS_ORDER (never regress; Hold/custom values are left alone)
  ordered/shipped/
  received         set to the event's date, only if blank
  est_arrival,
  lead_time        overwritten when the email gives a different value
  tracking         set if blank (else it goes in notes)
  order_no         filled if blank, when the row was matched by SKU
  notes            append a dated line (skipped if already present)
  never touched    qty, priority, budgets, quoted/actual, owner, flag, approved, client_review, links
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterator, List, Optional, Tuple

import gspread
from gspread.utils import rowcol_to_a1

from config import settings
from gmail_client import InboundEmail, get_credentials
from models import OrderUpdate

logger = logging.getLogger(__name__)

# Order of the sheet's Status dropdown, earliest to latest. "Hold" is deliberately absent.
STATUS_ORDER = ["Not Started", "Quote Requested", "Client Review", "Approved", "Ordered",
                "In Production", "Shipped", "Received", "Installed"]
_STATUS_CANON = {s.lower(): s for s in STATUS_ORDER}

EVENT_STATUS = {"order_confirmed": "Ordered", "in_production": "In Production",
                "shipped": "Shipped", "delivered": "Received"}
EVENT_DATE_COL = {"order_confirmed": "ordered", "shipped": "shipped", "delivered": "received"}
EVENT_LABEL = {"order_confirmed": "Order confirmed", "in_production": "In production", "shipped": "Shipped",
               "delivered": "Delivered", "delayed": "DELAYED", "backordered": "BACKORDERED",
               "cancelled": "CANCELLED", "other": "Update"}
ATTENTION_EVENTS = {"delayed", "backordered", "cancelled"}  # also surfaced on the Needs Review tab

HEADER_ALIASES = {
    "order": "order_no", "order_number": "order_no", "order_num": "order_no", "po": "order_no", "po_number": "order_no",
    "sku": "item_sku", "item_number": "item_sku",
    "tracking_number": "tracking", "tracking_no": "tracking", "tracking_num": "tracking",
    "estimate_arrival": "est_arrival", "estimated_arrival": "est_arrival", "eta": "est_arrival",
    "backup_op": "backup_link",
}


class SheetError(Exception):
    pass


@dataclass
class Plan:
    action: str  # "update" | "noop"
    row: Optional[int] = None  # 1-based sheet row
    values: Dict[str, str] = field(default_factory=dict)  # canonical header -> new cell value
    summary: str = ""


@dataclass
class Table:
    header_idx: int  # 0-based index of the header row within the values grid
    col: Dict[str, int]  # canonical header -> 0-based column index


@dataclass
class TrackedRow:
    row: int
    order_no: str
    sku: str
    room: str
    item: str
    status: str


def norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def norm_header(h: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")
    return HEADER_ALIASES.get(key, key)


def locate_table(rows: List[List[str]]) -> Table:
    for i, row in enumerate(rows[:30]):
        col = {norm_header(h): j for j, h in enumerate(row) if h.strip()}
        if "room" in col and "item" in col:
            return Table(i, col)
    raise SheetError("Could not find a header row containing both 'Room' and 'Item' in the first 30 rows.")


def table_rows(rows: List[List[str]], t: Table) -> Iterator[Tuple[int, List[str]]]:
    """Yield (1-based sheet row number, row) for every row below the header."""
    for i in range(t.header_idx + 1, len(rows)):
        yield i + 1, rows[i]


def _cell(row: List[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _s(value: object) -> str:
    """Stringify for USER_ENTERED writes; neutralise anything that would parse as a formula."""
    if value is None or value == "":
        return ""
    text = str(value).strip()
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _short_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return "%d/%d" % (d.month, d.day)
    except ValueError:
        return iso


def _valid_iso(text: Optional[str]) -> Optional[str]:
    try:
        return date.fromisoformat(text).isoformat() if text else None
    except ValueError:
        return None


def _same_date(displayed: str, iso: str) -> bool:
    """Does a displayed cell like '11/20' or '11/20/2026' denote the ISO date?"""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", displayed.strip())
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return False
    if not m:
        return displayed.strip() == iso
    if (int(m.group(1)), int(m.group(2))) != (d.month, d.day):
        return False
    if m.group(3):
        year = int(m.group(3))
        return (year + 2000 if year < 100 else year) == d.year
    return True


def tracked_rows(rows: List[List[str]]) -> List[TrackedRow]:
    """Rows that carry an order number or SKU: the only rows an email can update."""
    t = locate_table(rows)
    c = t.col
    out = []
    for n, r in table_rows(rows, t):
        order = _cell(r, c["order_no"]) if "order_no" in c else ""
        sku = _cell(r, c["item_sku"]) if "item_sku" in c else ""
        if (order or sku) and _cell(r, c["item"]):
            out.append(TrackedRow(n, order, sku, _cell(r, c["room"]), _cell(r, c["item"]),
                                  _cell(r, c["status"]) if "status" in c else ""))
    return out


def _note_line(upd: OrderUpdate, email_date: str, has_tracking_col: bool, scope_note: str) -> str:
    bits = [EVENT_LABEL[upd.event]]
    if upd.order_number:
        bits.append("order %s" % upd.order_number)
    if upd.carrier or (upd.tracking_number and not has_tracking_col):
        bits.append(" ".join(x for x in (upd.carrier, upd.tracking_number if not has_tracking_col else None) if x))
    if upd.notes:
        bits.append(upd.notes.strip())
    if scope_note:
        bits.append(scope_note)
    head = "[%s] %s: " % (_short_date(email_date), upd.vendor) if upd.vendor else "[%s] " % _short_date(email_date)
    return head + " | ".join(bits)


def plan_update(rows: List[List[str]], row_no: int, upd: OrderUpdate, email_date: str,
                fill_order_no: bool = False, scope_note: str = "") -> Plan:
    t = locate_table(rows)
    col = t.col
    row = rows[row_no - 1]

    def cur(h: str) -> str:
        return _cell(row, col[h]) if h in col else ""

    changes: Dict[str, str] = {}

    status = EVENT_STATUS.get(upd.event)
    if status and "status" in col:
        current = cur("status")
        current_c = _STATUS_CANON.get(norm(current))
        if current == "" or (current_c and STATUS_ORDER.index(status) > STATUS_ORDER.index(current_c)):
            changes["status"] = status

    event_date = _valid_iso(upd.event_date) or email_date
    dcol = EVENT_DATE_COL.get(upd.event)
    if dcol and dcol in col and not cur(dcol):
        changes[dcol] = event_date

    est = _valid_iso(upd.est_arrival)
    if est and "est_arrival" in col and upd.event != "delivered" and not _same_date(cur("est_arrival"), est):
        changes["est_arrival"] = est
    if upd.lead_time and "lead_time" in col and norm(upd.lead_time) != norm(cur("lead_time")):
        changes["lead_time"] = _s(upd.lead_time)

    has_tracking_col = "tracking" in col
    if upd.tracking_number and has_tracking_col and not cur("tracking"):
        changes["tracking"] = _s(upd.tracking_number)
    if fill_order_no and upd.order_number and "order_no" in col and not cur("order_no"):
        changes["order_no"] = _s(upd.order_number)

    note = _note_line(upd, email_date, has_tracking_col and bool(cur("tracking") or "tracking" in changes), scope_note)
    if "notes" in col and note.split("] ", 1)[-1] not in cur("notes"):
        prior = cur("notes")
        changes["notes"] = _s(prior + "\n" + note if prior else note)

    label = "row %d (%s / %s)" % (row_no, _cell(row, col["room"]), _cell(row, col["item"]))
    if not changes:
        return Plan("noop", row_no, {}, "no change: " + label)
    return Plan("update", row_no, changes, "update %s: %s" % (label, ", ".join(changes)))


def sheet_key(ref: Optional[str]) -> str:
    """Accept a bare spreadsheet ID or a full sheet URL."""
    if not ref:
        raise SheetError(
            "SHEET_ID is not set. Put the ID from your sheet's URL in .env: "
            "docs.google.com/spreadsheets/d/<THIS PART>/edit  (opening by name needs extra Drive permission)"
        )
    m = re.search(r"/d/([A-Za-z0-9_-]+)", ref)
    return m.group(1) if m else ref.strip()


def _optional_tab(sh, name: str):
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return None


class ReviewTab:
    """The 'Needs Review' tab: emails the app could not place, or that need a human's attention."""

    HEADERS = ["Received", "From", "Subject", "Gmail link", "Order #", "SKUs / items", "Event", "Reason",
               "Proposed update", "Resolved?", "Message ID"]
    KEY_COL = len(HEADERS)  # 1-based column of the Message ID
    RESOLVED_COL = HEADERS.index("Resolved?") + 1

    def __init__(self, ws) -> None:
        self.ws = ws

    @staticmethod
    def key(email: InboundEmail, index: int) -> str:
        return "%s:%d" % (email.id, index)

    def _ensure_headers(self) -> None:
        if not any(c.strip() for c in self.ws.row_values(1)):
            self.ws.update(range_name="A1", values=[self.HEADERS], value_input_option="RAW")

    def add(self, email: InboundEmail, upd: OrderUpdate, index: int, reason: str, proposed: str = "") -> None:
        if self.ws is None:
            logger.warning("No '%s' tab; review item not recorded: %s", settings.review_tab, reason)
            return
        self._ensure_headers()
        key = self.key(email, index)
        if key in self.ws.col_values(self.KEY_COL):
            return
        items = "; ".join(" ".join(x for x in (l.sku, l.name) if x) for l in upd.lines)[:200]
        self.ws.append_row([
            email.date_iso, email.sender, email.subject, "https://mail.google.com/mail/u/0/#all/%s" % email.thread_id,
            upd.order_number or "", items, EVENT_LABEL[upd.event], reason, proposed, "", key,
        ], value_input_option="RAW")

    def mark_resolved(self, key: str, text: str) -> None:
        if self.ws is None:
            return
        keys = self.ws.col_values(self.KEY_COL)
        if key in keys:
            self.ws.update_cell(keys.index(key) + 1, self.RESOLVED_COL, text)


class LogTab:
    """Optional 'Log' tab: one line per applied update. Only used if the tab exists."""

    HEADERS = ["Time", "From", "Subject", "Order #", "Event", "Rows", "Changes"]

    def __init__(self, ws) -> None:
        self.ws = ws

    def add(self, email: InboundEmail, upd: OrderUpdate, plans: List[Plan]) -> None:
        if self.ws is None:
            return
        if not any(c.strip() for c in self.ws.row_values(1)):
            self.ws.update(range_name="A1", values=[self.HEADERS], value_input_option="RAW")
        changes = " || ".join("row %s: %s" % (p.row, ", ".join(p.values) or "no change") for p in plans)
        self.ws.append_row([date.today().isoformat(), email.sender, email.subject, upd.order_number or "",
                            EVENT_LABEL[upd.event], ", ".join(str(p.row) for p in plans), changes[:500]],
                           value_input_option="RAW")


class SheetClient:
    def __init__(self) -> None:
        gc = gspread.authorize(get_credentials())
        sh = gc.open_by_key(sheet_key(settings.sheet_id))
        self.ws = sh.worksheet(settings.worksheet_name) if settings.worksheet_name else sh.sheet1
        self.review = ReviewTab(_optional_tab(sh, settings.review_tab))
        self.log = LogTab(_optional_tab(sh, settings.log_tab))

    def read(self) -> List[List[str]]:
        return self.ws.get_all_values()

    def apply(self, rows: List[List[str]], plan: Plan) -> None:
        if plan.action != "update":
            return
        col = locate_table(rows).col
        data = [
            {"range": rowcol_to_a1(plan.row, col[h] + 1), "values": [[v]]}
            for h, v in plan.values.items()
        ]
        self.ws.batch_update(data, value_input_option="USER_ENTERED")
