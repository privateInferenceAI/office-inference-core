"""The chunker's rules, tested against documents shaped like the parser's real output.

These are unit tests over synthetic documents rather than a run against the live parser, because the
rules are what need pinning: page bounding, table joining, header folding, and deterministic ids. The
live run is `docs/runbook.md` step 3.5, and its job is to show the rules hold on a real scan.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import chunker  # noqa: E402  (the import needs the path above)

# Column extents taken from the real parse of the fixture, so the geometry the joiner sees is the
# geometry it sees on the box: the left fragment's rate column ends at 297 and the right fragment's item
# column starts at 316, which is a 19 point gap and why the tolerance is not smaller than that.
COLUMNS_LEFT = [(54, 170), (179, 230), (237, 297)]
COLUMNS_RIGHT = [(316, 432), (441, 492), (499, 558)]
HEADER = ["Item", "Basis", "Rate"]


def cell(text: str, row: int, col: int, span: tuple[int, int]) -> dict:
    """One cell, with a box placed the way a page places it.

    Row zero is at the TOP of the table, so its vertical value is the largest and each row below it is
    smaller. Getting this backwards is easy and was, once: the chunker finds a table's header by the
    topmost row's position rather than by its row index, so a fixture whose boxes descend the wrong way
    hands it the last row as the header and the column names come out attached to the wrong values.
    """
    left, right = span
    top = 700 - row * 12
    return {
        "text": text,
        "start_row_offset_idx": row,
        "start_col_offset_idx": col,
        "bbox": {"l": left, "r": right, "t": top, "b": top - 12,
                 "coord_origin": "BOTTOMLEFT"},
    }


def fragment(ref: str, page: int, rows: list[list[str]], columns: list[tuple[int, int]]) -> dict:
    cells = [cell(text, row_index, col_index, columns[col_index])
             for row_index, row in enumerate(rows)
             for col_index, text in enumerate(row)]
    return {
        "self_ref": ref,
        "prov": [{"page_no": page,
                  "bbox": {"l": columns[0][0], "r": columns[-1][1], "t": 710, "b": 640,
                           "coord_origin": "BOTTOMLEFT"}}],
        "data": {"num_rows": len(rows), "num_cols": len(columns), "table_cells": cells},
    }


def split_schedule_document() -> dict:
    """The fixture's shape: one logical schedule emitted as two fragments, the second without a header."""
    left = fragment("#/tables/0", 1, [HEADER,
                    ["Mileage, client-owned vehicle", "per mile", "0.67"],
                    ["Standard labour, business hours", "per hour", "145.00"],
                    ["After-hours multiplier", "of labour", "1.5x"]], COLUMNS_LEFT)
    right = fragment("#/tables/1", 1, [
        ["Emergency attendance", "per callout", "240.00"],
        ["Callout fee, scheduled visit", "per visit", "89.00"],
        ["Filter media, standard", "per unit", "32.50"],
        ["Refrigerant, R-410A", "per pound", "18.75"]], COLUMNS_RIGHT)
    return {
        # Real parser output always carries a pages object, and its absence is what marks a document as
        # having no page structure. The fixtures that omitted it were unrealistic and made every document
        # look like HTML.
        "pages": {"1": {}},
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"},
                              {"$ref": "#/tables/0"}, {"$ref": "#/tables/1"}]},
        "texts": [
            {"self_ref": "#/texts/0", "label": "title", "text": "Meridian", "level": 1,
             "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 745, "b": 730}}]},
            {"self_ref": "#/texts/1", "label": "paragraph", "text": "Fees. " * 4,
             "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 700, "b": 660}}]},
        ],
        "tables": [left, right],
    }


def test_a_table_split_by_the_parser_is_joined_before_anything_else():
    """Found by measurement in step 3.4: the parser emits one table per column region, so a schedule
    longer than its column arrives as two tables. The architecture says never split a table, and that
    rule is meaningless unless the two fragments are one table again first."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) == 1, "the two fragments should be one table"
    body = tables[0].text
    assert "Mileage, client-owned vehicle" in body
    assert "Refrigerant, R-410A" in body, "the second fragment's rows are missing"


def test_no_data_row_is_lost_to_the_header_rule():
    """The bug this test exists for. Only a fragment whose top row IS the header may have that row
    dropped. Passing skip_first for every fragment after the first deleted a real fee row, and the
    table still looked plausible."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    rows = [c for c in chunks if c.kind == "table_row"]
    assert len(rows) == 7, f"seven fee rows expected, got {len(rows)}"
    joined = " ".join(r.text for r in rows)
    for item in ("Mileage", "Standard labour", "After-hours", "Emergency attendance",
                 "Callout fee", "Filter media", "Refrigerant"):
        assert item in joined, f"{item} was dropped"


def test_every_row_entry_carries_its_column_names():
    """A row is retrieved and cited alone, so it has to make sense alone. Without the folded names a
    row reads as '0.67' with nothing to say what it is the rate for."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    rows = [c for c in chunks if c.kind == "table_row"]
    for row in rows:
        assert "Item:" in row.text and "Rate:" in row.text, row.text
    rate_row = next(r for r in rows if "145.00" in r.text)
    assert "Standard labour" in rate_row.text and "per hour" in rate_row.text


def test_the_header_row_is_not_a_retrievable_row():
    """It carries column names and no value, so retrieving it answers nothing and cites the schedule's
    shape rather than a fee. It stays inside the whole-table entry."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    rows = [c for c in chunks if c.kind == "table_row"]
    assert not any(r.text.strip() == " | ".join(HEADER) for r in rows)


def test_table_rows_are_not_inside_the_page_chunks():
    """The architecture requires it: a figure that sits in two retrievable places gets two different
    citations, and one of them will be wrong."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    prose = " ".join(c.text for c in chunks if c.kind == "prose")
    assert "145.00" not in prose, "a fee appears in a page chunk as well as in the table"
    assert "Refrigerant" not in prose


def test_chunk_ids_are_deterministic_and_unique():
    """A re-run has to produce the same ids, or a re-ingest duplicates a collection instead of updating
    it, and the duplicate is the one with the stale page number."""
    document = split_schedule_document()
    first = chunker.chunk_document(document, "doc", "sha")
    second = chunker.chunk_document(document, "doc", "sha")
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)


def test_a_chunker_change_makes_new_ids():
    """The version is inside the id on purpose: it is what forces a deliberate re-embed rather than a
    silent mix of two chunkings in one collection."""
    document = split_schedule_document()
    before = chunker.chunk_document(document, "doc", "sha")[0].chunk_id
    original = chunker.CHUNKER_VERSION
    try:
        chunker.CHUNKER_VERSION = original + "-test"
        after = chunker.chunk_document(document, "doc", "sha")[0].chunk_id
    finally:
        chunker.CHUNKER_VERSION = original
    assert before != after


def test_prose_never_crosses_a_page():
    """The page is the citation unit, so a chunk that spans two pages would produce a citation that is
    right about one page and wrong about the other."""
    texts = [
        {"self_ref": f"#/texts/{i}", "label": "paragraph", "text": f"Page {page} sentence. " * 10,
         "prov": [{"page_no": page, "bbox": {"l": 53, "t": 700, "b": 660}}]}
        for i, page in enumerate((1, 1, 2, 2))
    ]
    document = {"pages": {str(p): {} for p in (1, 2)},
                "body": {"children": [{"$ref": t["self_ref"]} for t in texts]}, "texts": texts,
                "tables": []}
    chunks = chunker.chunk_document(document, "doc", "sha")
    for chunk in chunks:
        assert chunk.page_start == chunk.page_end, chunk.text


def test_neighbours_are_linked_both_ways():
    """The escape hatch the architecture chose over a parent pointer, because it needs no re-embed."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    assert chunks[0].prev_chunk_id is None
    assert chunks[-1].next_chunk_id is None
    for index in range(len(chunks) - 1):
        assert chunks[index].next_chunk_id == chunks[index + 1].chunk_id
        assert chunks[index + 1].prev_chunk_id == chunks[index].chunk_id


def test_the_token_estimate_is_documented_as_an_estimate():
    """It bounds a chunk size and must never be used to assert one, because it is words times a
    constant rather than the embedding model's tokenizer."""
    assert chunker.estimate_tokens("one two three") == 4
    assert chunker.estimate_tokens("") == 1
    text = "word " * 300
    assert chunker.MIN_TOKENS <= chunker.estimate_tokens(text) <= 500


def test_labelling_survives_the_parser_emitting_cells_in_any_order():
    """The test that found two real bugs, so it stays.

    Pairing column names to cells positionally without sorting first produced "per callout: Item".
    Looking the name up by the cell's own column index produced a rotated row, because the second
    fragment of a split table does not share the first fragment's column numbering. Both were invisible
    until the input was shuffled, because the parser happens to emit cells in reading order.

    A row's cells sorted by column number ARE reading order, so position within the sorted row is the
    reliable key and the column index is only a sort key.
    """
    import random

    expected = {
        "Mileage, client-owned vehicle": ("per mile", "0.67"),
        "Standard labour, business hours": ("per hour", "145.00"),
        "After-hours multiplier": ("of labour", "1.5x"),
        "Emergency attendance": ("per callout", "240.00"),
        "Callout fee, scheduled visit": ("per visit", "89.00"),
        "Filter media, standard": ("per unit", "32.50"),
        "Refrigerant, R-410A": ("per pound", "18.75"),
    }
    for seed in (1, 7, 42, 99):
        document = split_schedule_document()
        for table in document["tables"]:
            random.Random(seed).shuffle(table["data"]["table_cells"])
        chunks = chunker.chunk_document(document, "doc", "sha")
        rows = [c.text for c in chunks if c.kind == "table_row"]
        for item, (basis, rate) in expected.items():
            match = [text for text in rows if f"Item: {item}" in text]
            assert match, f"seed {seed}: no row for {item}"
            assert f"Basis: {basis}" in match[0], f"seed {seed}: {match[0]!r}"
            assert f"Rate: {rate}" in match[0], f"seed {seed}: {match[0]!r}"


def test_a_single_item_larger_than_the_budget_is_split_not_appended():
    """The bug this test exists for.

    The first version tested only whether adding the NEXT item would overflow. An item already over the
    budget therefore went in whole and the chunk came out past the ceiling, which on a dense page is
    exactly what the ceiling exists to prevent: something the next stage has to reject. A real page of
    800-token prose would have produced an unembeddable-by-budget chunk while every test passed.
    """
    huge = "This is a sentence about fees and schedules. " * 90      # well over 512 tokens
    document = {
        "pages": {"1": {}},
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [{"self_ref": "#/texts/0", "label": "paragraph", "text": huge,
                   "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 700, "b": 660}}]}],
        "tables": [],
    }
    chunks = chunker.chunk_document(document, "doc", "sha")
    assert len(chunks) > 1, "an oversized page must become more than one chunk"
    for chunk in chunks:
        assert chunker.estimate_tokens(chunk.text) <= chunker.MAX_TOKENS, (
            f"a chunk came out over the ceiling at {chunker.estimate_tokens(chunk.text)} tokens")


def test_splitting_happens_at_sentence_boundaries():
    """A citation that quotes half a sentence reads as a misquote, so a split lands between sentences."""
    paragraph = "The rate is 67 cents per mile. " * 60
    pieces = chunker.split_oversized(paragraph, [], 1, {})
    assert len(pieces) > 1
    for piece in pieces[:-1]:
        assert piece.rstrip().endswith("."), f"split mid-sentence: {piece[-40:]!r}"


def test_a_page_that_fits_stays_one_chunk():
    """The ceiling is a ceiling, not a target. Padding a short page up to 384 tokens would mean
    inventing text, and splitting a page that fits would cost a citation its context."""
    document = {
        "pages": {"1": {}},
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [{"self_ref": "#/texts/0", "label": "paragraph", "text": "A short policy note.",
                   "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 700, "b": 660}}]}],
        "tables": [],
    }
    chunks = chunker.chunk_document(document, "doc", "sha")
    assert len(chunks) == 1
    assert chunker.estimate_tokens(chunks[0].text) < chunker.MIN_TOKENS


def test_a_document_with_no_pages_is_retrieved_rather_than_dropped():
    """The cause the probe found, which was not the cause I had guessed.

    Two HTML contract exhibits were lost on the first real corpus run. They parse to 158 text items with no
    page and no bounding box, because HTML has no pages. The chunker dropped every one of them for having
    no page number, since the page is the citation unit. The body was complete and the traversal worked:
    the documents simply have no pages to cite.

    Dropping them is the one outcome that cannot be explained to a client. The text is there, the question
    is answerable, and the answer says nothing. The whole document becomes page 1, marked nominal.
    """
    document = {
        "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(4)]},
        "texts": [{"self_ref": f"#/texts/{i}", "label": "text" if i else "title",
                   "text": f"Clause {i}: the parties agree to the following terms."}
                  # No prov at all, which is what HTML gives.
                  for i in range(4)],
        "tables": [],
        # No pages key, which is the signal.
    }
    chunks = chunker.chunk_document(document, "doc", "sha")
    assert chunks, "a pageless document must still be retrievable"
    for chunk in chunks:
        assert chunk.page_start == 1 and chunk.page_end == 1
        assert chunk.page_is_nominal is True, "page 1 of a pageless file is nominal, not measured"


def test_a_document_with_pages_is_not_marked_nominal():
    """The flag has to mean something: a scanned page and page 1 of an HTML file are not the same claim."""
    chunks = chunker.chunk_document(split_schedule_document(), "doc", "sha")
    for chunk in chunks:
        assert chunk.page_is_nominal is False
