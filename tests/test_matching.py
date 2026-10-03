import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching import id_tokens, match_update, norm_id
from models import LineItem, OrderUpdate

HEADER = ["Room", "Order No", "Item SKU", "Item", "Status"]


def build(*data):
    return [[], ["title"], HEADER] + [list(d) for d in data]  # header on sheet row 3; data from row 4


ROWS = build(
    ["Foyer", "SO-4412", "UBUD-60", "Console table", "Ordered"],      # 4
    ["Foyer", "SO-4412", "FRES-1", "Bench", "Ordered"],               # 5
    ["Great Room", "", "LAMP-9", "Floor lamp", "Not Started"],        # 6
    ["Study", "A100, B200", "DESK-1", "Desk", "Ordered"],             # 7
    ["Study", "C300", "CHAIR-1", "Chair", "Ordered"],                 # 8
    ["Den", "", "CHAIR-1", "Chair", "Not Started"],                   # 9
)


def upd(**kw):
    kw.setdefault("event", "shipped")
    return OrderUpdate(**kw)


def test_norm_id_and_tokens():
    assert norm_id("#SO 4412") == norm_id("so-4412") == "so4412"
    assert id_tokens("A100, B200") == {"a100", "b200"}
    assert id_tokens("A100 and B200; C300") == {"a100", "b200", "c300"}


def test_order_number_with_sku_narrows_to_that_item():
    m = match_update(ROWS, upd(order_number="so4412", lines=[LineItem(sku="FRES 1")]))
    assert (m.rows, m.how, m.whole_order) == ([5], "order", False)


def test_order_number_without_items_covers_whole_order():
    m = match_update(ROWS, upd(order_number="SO-4412"))
    assert m.rows == [4, 5] and m.whole_order and m.how == "order"


def test_multi_order_cell_matches_any_of_its_numbers():
    assert match_update(ROWS, upd(order_number="B200")).rows == [7]


def test_names_only_lines_use_llm_choice_but_only_among_candidates():
    lines = [LineItem(name="Bench")]
    assert match_update(ROWS, upd(order_number="SO-4412", lines=lines, matched_rows=[5])).rows == [5]
    # the model pointing at an unrelated row (6) must be ignored
    m = match_update(ROWS, upd(order_number="SO-4412", lines=lines, matched_rows=[6]))
    assert m.rows == [] and "told apart" in m.reason
    assert match_update(ROWS, upd(order_number="SO-4412", lines=lines)).rows == []


def test_sku_match_on_row_without_order_number():
    m = match_update(ROWS, upd(order_number="W-77", lines=[LineItem(sku="LAMP-9")]))
    assert (m.rows, m.how) == ([6], "sku")      # caller fills in the order # for sku matches


def test_sku_on_row_with_different_order_number_is_not_matched():
    m = match_update(ROWS, upd(order_number="Z999", lines=[LineItem(sku="DESK-1")]))
    assert m.rows == [] and "different order number" in m.reason


def test_sku_on_several_rows_is_ambiguous_unless_llm_picks():
    lines = [LineItem(sku="CHAIR-1")]
    m = match_update(ROWS, upd(order_number="Q1", lines=lines))
    assert m.rows == [9]                         # row 8 belongs to order C300, so only row 9 qualifies
    rows = build(["A", "", "CH", "Chair", ""], ["B", "", "CH", "Chair", ""])
    assert match_update(rows, upd(lines=[LineItem(sku="CH")])).rows == []
    assert match_update(rows, upd(lines=[LineItem(sku="CH")], matched_rows=[5])).rows == [5]


def test_unknown_order_and_empty_email():
    assert "not on the sheet yet" in match_update(ROWS, upd(order_number="NOPE")).reason
    assert "no order number or SKU" in match_update(ROWS, upd()).reason


def test_sheet_without_order_columns():
    rows = [["Room", "Item"], ["A", "B"]]
    assert "no Order # or SKU column" in match_update(rows, upd(order_number="1")).reason
