"""Decide which sheet rows an extracted update belongs to.

Deterministic first: Order # (strong), then SKU. The LLM may only choose *among* rows those two
checks already allow, so it can never point an update at an unrelated row.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Set

from models import OrderUpdate
from sheets import _cell, locate_table, table_rows


@dataclass
class Match:
    rows: List[int]
    how: str = ""  # "order" | "sku"
    reason: str = ""  # why nothing matched, when rows is empty
    whole_order: bool = False  # email names no specific items: applies to every row on the order


def norm_id(s: Optional[str]) -> str:
    """'SO-1234', 'so 1234' and '#SO1234' all become 'so1234'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def id_tokens(cell: str) -> Set[str]:
    """A cell may hold several ids ('A123, B456')."""
    parts = re.split(r"[,;\n&]+|\band\b", cell, flags=re.I)
    return {t for t in (norm_id(p) for p in parts) if t}


def match_update(rows: List[List[str]], upd: OrderUpdate) -> Match:
    t = locate_table(rows)
    col = t.col
    if "order_no" not in col and "item_sku" not in col:
        return Match([], reason="the sheet has no Order # or SKU column")

    oid = norm_id(upd.order_number)
    skus = {norm_id(l.sku) for l in upd.lines if l.sku} - {""}
    if not oid and not skus:
        return Match([], reason="the email has no order number or SKU")

    by_order = []  # (row, row's sku matches one of the email's skus)
    by_sku_only = []
    sku_other_order = []
    for n, row in table_rows(rows, t):
        order_toks = id_tokens(_cell(row, col["order_no"])) if "order_no" in col else set()
        sku_toks = id_tokens(_cell(row, col["item_sku"])) if "item_sku" in col else set()
        sku_hit = bool(skus & sku_toks)
        if oid and oid in order_toks:
            by_order.append((n, sku_hit))
        elif sku_hit:
            (by_sku_only if not order_toks else sku_other_order).append(n)

    narrowed = False
    if by_order:
        how = "order"
        hits = [n for n, h in by_order if h]
        narrowed = bool(hits)
        rows_ = hits or [n for n, _ in by_order]
    elif by_sku_only:
        how, rows_ = "sku", by_sku_only
    elif sku_other_order:
        return Match([], reason="the SKU is on the sheet only under a different order number")
    else:
        what = "order number %s" % upd.order_number if oid else "SKU %s" % ", ".join(sorted(skus))
        return Match([], reason="%s is not on the sheet yet" % what)

    if len(rows_) == 1:
        return Match(rows_, how)

    # Several candidate rows. Without item detail, an order-level update covers the whole order.
    if not upd.lines:
        if how == "order":
            return Match(rows_, how, whole_order=True)
        return Match([], reason="the SKU is on several rows and the email doesn't say which")
    if how == "order" and narrowed:
        return Match(rows_, how)  # the email's SKUs identified specific rows within the order
    chosen = [n for n in rows_ if n in set(upd.matched_rows)]
    if not chosen:
        return Match([], reason="it matches several rows and the items could not be told apart")
    return Match(chosen, how)
