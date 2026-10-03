import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from models import OrderUpdate
from sheets import (SheetError, _same_date, locate_table, plan_update, sheet_key, tracked_rows)

# Mirrors the real tab: title rows, header on row 6, a section row, a TOTAL row.
HEADER = ["Room", "Order No", "Item SKU", "Flag", "Category", "Item", "Priority", "Qty", "Unit Budget",
          "Item Budget", "Quoted / Actual", "Link", "Backup Op.", "Lead Time", "Estimate Arrival", "Owner",
          "Status", "Ordered", "Shipped", "Received", "Notes"]


def row(**kw):
    return [str(kw.get(h, "")) for h in HEADER]


ROWS = [
    [], ["", "Procurement Detail"], [], [], [],
    HEADER,                                                                                                  # 6
    row(Room="Foyer / Entry", **{"Order No": "4412", "Item SKU": "UBUD-60", "Item": "Console table",
                                 "Status": "Ordered", "Ordered": "10/2", "Notes": "Confirm wall width",
                                 "Qty": "1", "Unit Budget": "$4,375"}),                                       # 7
    row(Room="Foyer / Entry", **{"Order No": "4412", "Item SKU": "FRES-1", "Item": "Bench", "Status": "Ordered"}),  # 8
    row(Room="Great Room", **{"Item": "Floor lamp", "Status": "Not Started"}),                               # 9
    row(Room="TOTAL"),                                                                                       # 10
]


def upd(**kw):
    kw.setdefault("event", "shipped")
    return OrderUpdate(**kw)


def test_locates_header_with_aliases():
    t = locate_table(ROWS)
    assert t.header_idx == 5
    assert t.col["order_no"] == 1 and t.col["item_sku"] == 2 and t.col["est_arrival"] == 14
    assert t.col["backup_link"] == 12


def test_tracked_rows_only_includes_rows_with_order_or_sku():
    assert [(r.row, r.order_no, r.sku) for r in tracked_rows(ROWS)] == [(7, "4412", "UBUD-60"), (8, "4412", "FRES-1")]


def test_shipped_sets_status_date_tracking_note():
    p = plan_update(ROWS, 7, upd(order_number="4412", vendor="Arhaus", carrier="UPS", tracking_number="1Z999",
                                 est_arrival="2026-11-03"), "2026-10-20")
    v = p.values
    assert v["status"] == "Shipped"
    assert v["shipped"] == "2026-10-20"                      # date column, set from the email's date
    assert v["est_arrival"] == "2026-11-03"
    assert v["notes"] == "Confirm wall width\n[10/20] Arhaus: Shipped | order 4412 | UPS 1Z999"
    assert "ordered" not in v and "received" not in v
    protected = {"qty", "priority", "unit_budget", "item_budget", "quoted_actual", "owner", "flag", "link"}
    assert not protected & set(v)


def test_event_date_from_email_text_beats_email_date_and_garbage_is_ignored():
    assert plan_update(ROWS, 7, upd(event_date="2026-10-18"), "2026-10-20").values["shipped"] == "2026-10-18"
    assert plan_update(ROWS, 7, upd(event_date="last Tuesday"), "2026-10-20").values["shipped"] == "2026-10-20"


def test_status_never_regresses_and_dates_are_not_overwritten():
    rows = [list(r) for r in ROWS]
    t = locate_table(rows)
    rows[6][t.col["status"]] = "Received"
    rows[6][t.col["shipped"]] = "10/10"
    p = plan_update(rows, 7, upd(event="shipped"), "2026-10-20")
    assert "status" not in p.values and "shipped" not in p.values


def test_delivered_sets_received_and_does_not_touch_estimate():
    p = plan_update(ROWS, 7, upd(event="delivered", est_arrival="2026-11-03"), "2026-11-04")
    assert p.values["status"] == "Received" and p.values["received"] == "2026-11-04"
    assert "est_arrival" not in p.values


def test_delay_changes_estimate_and_notes_but_not_status():
    p = plan_update(ROWS, 7, upd(event="delayed", est_arrival="2026-12-01", notes="Finish backordered"), "2026-10-20")
    assert "status" not in p.values
    assert p.values["est_arrival"] == "2026-12-01"
    assert "DELAYED" in p.values["notes"] and "Finish backordered" in p.values["notes"]


def test_confirmation_sets_ordered_only_if_blank_and_fills_order_no_for_sku_matches():
    p = plan_update(ROWS, 9, upd(event="order_confirmed", order_number="W-77"), "2026-10-02", fill_order_no=True)
    assert p.values["status"] == "Ordered" and p.values["ordered"] == "2026-10-02"
    assert p.values["order_no"] == "W-77"
    p2 = plan_update(ROWS, 7, upd(event="order_confirmed", order_number="4412"), "2026-10-02", fill_order_no=True)
    assert "ordered" not in p2.values and "order_no" not in p2.values   # already set


def test_whole_order_scope_note():
    p = plan_update(ROWS, 8, upd(order_number="4412"), "2026-10-20", scope_note="applies to all 2 items on the order")
    assert "applies to all 2 items on the order" in p.values["notes"]


def test_idempotent_after_applying():
    u = upd(order_number="4412", vendor="Arhaus", carrier="UPS", tracking_number="1Z999", est_arrival="2026-11-03")
    p = plan_update(ROWS, 7, u, "2026-10-20")
    applied = [list(r) for r in ROWS]
    t = locate_table(applied)
    for h, val in p.values.items():
        applied[6][t.col[h]] = val
    assert plan_update(applied, 7, u, "2026-10-20").action == "noop"
    # a sheet that displays the date as m/d must not look "changed" either
    applied[6][t.col["est_arrival"]] = "11/3"
    assert plan_update(applied, 7, u, "2026-10-20").action == "noop"


def test_tracking_column_used_when_present():
    rows = [r + [""] if i != 5 else r + ["Tracking"] for i, r in enumerate(ROWS)]
    p = plan_update(rows, 7, upd(carrier="UPS", tracking_number="1Z999"), "2026-10-20")
    assert p.values["tracking"] == "1Z999"
    assert "1Z999" not in p.values["notes"]


def test_formula_injection_is_neutralised():
    p = plan_update(ROWS, 7, upd(lead_time="=HYPERLINK(\"http://evil\")", tracking_number="+1"), "2026-10-20")
    assert p.values["lead_time"].startswith("'=")


@pytest.mark.parametrize("shown,iso,expected", [("11/20", "2026-11-20", True), ("11/20/2026", "2026-11-20", True),
                                                 ("11/21", "2026-11-20", False), ("11/20/2025", "2026-11-20", False),
                                                 ("2026-11-20", "2026-11-20", True), ("", "2026-11-20", False)])
def test_same_date(shown, iso, expected):
    assert _same_date(shown, iso) is expected


def test_missing_header_row_raises():
    with pytest.raises(SheetError):
        locate_table([["foo", "bar"], ["a", "b"]])


def test_sheet_key_accepts_id_or_url():
    assert sheet_key("1AbC_dEf-123") == "1AbC_dEf-123"
    assert sheet_key("https://docs.google.com/spreadsheets/d/1AbC_dEf-123/edit#gid=0") == "1AbC_dEf-123"
    with pytest.raises(SheetError):
        sheet_key(None)
