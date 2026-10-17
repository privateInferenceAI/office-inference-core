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
    pieces = chunker.split_oversized(paragraph, 1, {})
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


# --------------------------------------------------------------------------- the heading is content

def scan_document(title: str, body: list[str]) -> dict:
    """A scanned page's shape: a title line, then the body, and nothing else.

    This is what OCR returns for an invoice, and it is the case that exposed the defect. The identifiers a
    person searches for -- the supplier, the invoice number -- live in the title line and appear nowhere
    else in the document, so a heading treated as structure is a document with no identity.
    """
    texts = [{"self_ref": "#/texts/0", "label": "title", "text": title, "level": 1,
              "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 740, "b": 700}}]}]
    texts += [{"self_ref": f"#/texts/{i + 1}", "label": "text", "text": line,
               "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 690 - i * 40, "b": 660 - i * 40}}]}
              for i, line in enumerate(body)]
    return {
        "pages": {"1": {}},
        "body": {"children": [{"$ref": t["self_ref"]} for t in texts]},
        "texts": texts,
        "tables": [],
    }


def test_a_fact_that_exists_only_in_the_title_is_in_the_chunk_text():
    """**The test that was missing, and the defect it would have caught.**

    `page_texts` treats an item labelled `title` or `section_header` as structure, so the line becomes a
    `headings` breadcrumb and never content. The breadcrumb travels into the vector payload's `headings`
    field, and **neither retrieval arm reads that field**: the embedding is of `chunk.text` and the lexical
    index is of `payload["text"]`.

    Measured on a real scan before the fix: the OCR returned
    `HALLORAN MECHANICAL LTD INVOICE 2026-0417` correctly, the payload carried it, and `chunk_index` held
    zero rows matching `HALLORAN` and zero matching `2026-0417` against four matching the amount that
    happened to sit in the body. **The invoice could not be found by its own number.**
    """
    document = scan_document("HALLORAN MECHANICAL LTD INVOICE 2026-0417",
                             ["Site: Lake Kayla pumping station",
                              "Total due: 4,218.50"])
    chunks = chunker.chunk_document(document, "company/scan.png", "sha")
    prose = [c for c in chunks if c.kind == "prose"]
    assert prose, "the scan produced no prose chunk"
    joined = "\n".join(c.text for c in prose)
    assert "HALLORAN" in joined, "the supplier name is not searchable"
    assert "2026-0417" in joined, "the invoice number is not searchable"
    assert "4,218.50" in joined, "the body must still be there"


def test_a_section_header_reaches_the_chunks_under_it():
    """The same defect on an ordinary document, which is most of them.

    A generated record has one title and four section headers out of fourteen text items; a contract in the
    corpus has sixty section headers out of a hundred and twenty-one. Every one of those lines was structure
    and none of it was searchable.
    """
    texts = [
        {"self_ref": "#/texts/0", "label": "title", "text": "Planned maintenance schedule: Lake Kayla",
         "level": 1, "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 800, "b": 780}}]},
        {"self_ref": "#/texts/1", "label": "section_header", "text": "Boiler", "level": 2,
         "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 760, "b": 740}}]},
        {"self_ref": "#/texts/2", "label": "text", "text": "Last attended on 12 April 2026.",
         "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 730, "b": 710}}]},
    ]
    document = {"pages": {"1": {}}, "tables": [],
                "body": {"children": [{"$ref": t["self_ref"]} for t in texts]},
                "texts": texts}
    joined = "\n".join(c.text for c in chunker.chunk_document(document, "doc", "sha"))
    assert "Boiler" in joined, "a section header is not searchable"
    assert "Planned maintenance schedule" in joined, "the document title is not searchable"


def test_the_heading_path_is_the_one_the_chunk_opens_under():
    """One prefix per chunk, not per item: a chunk that runs past a new section belongs to the one it began in.

    The alternative -- re-prefixing mid-chunk -- would put two section names in one retrievable passage and
    make the citation's breadcrumb disagree with its own text.
    """
    texts = [
        {"self_ref": "#/texts/0", "label": "section_header", "text": "First", "level": 1,
         "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 800, "b": 780}}]},
        {"self_ref": "#/texts/1", "label": "text", "text": "Under the first heading.",
         "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 770, "b": 750}}]},
        {"self_ref": "#/texts/2", "label": "section_header", "text": "Second", "level": 1,
         "prov": [{"page_no": 2, "bbox": {"l": 0, "t": 800, "b": 780}}]},
        {"self_ref": "#/texts/3", "label": "text", "text": "Under the second heading.",
         "prov": [{"page_no": 2, "bbox": {"l": 0, "t": 770, "b": 750}}]},
    ]
    document = {"pages": {"1": {}, "2": {}}, "tables": [],
                "body": {"children": [{"$ref": t["self_ref"]} for t in texts]},
                "texts": texts}
    chunks = [c for c in chunker.chunk_document(document, "doc", "sha") if c.kind == "prose"]
    assert len(chunks) == 2, "the two pages must not share a chunk"
    assert chunks[0].text.startswith("First"), chunks[0].text[:40]
    assert chunks[1].text.startswith("Second"), chunks[1].text[:40]
    assert "Second" not in chunks[0].text, "a chunk carries the heading it opened under and no other"


def test_a_document_with_no_headings_is_unchanged():
    """The regression guard for every document that has no headings at all.

    The prefix is added when there is a heading path and skipped when there is not, so a plain text file
    produces exactly the text it produced before. Without this the fix would quietly prepend an empty line to
    every chunk of every document.
    """
    texts = [{"self_ref": f"#/texts/{i}", "label": "text", "text": f"Sentence number {i}.",
              "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 700 - i * 20, "b": 680 - i * 20}}]}
             for i in range(3)]
    document = {"pages": {"1": {}}, "tables": [],
                "body": {"children": [{"$ref": t["self_ref"]} for t in texts]},
                "texts": texts}
    chunks = chunker.chunk_document(document, "doc", "sha")
    assert chunks, "a document with no headings must still chunk"
    assert chunks[0].text == "Sentence number 0.\n\nSentence number 1.\n\nSentence number 2."


def test_the_headings_field_still_carries_the_path_for_the_citation():
    """The fix adds the heading to the text; it does not remove it from the field the citation panel reads.

    Both are wanted. The text is what search sees; the field is what a citation displays as its breadcrumb,
    and a change that made search work by emptying the breadcrumb would trade one visible thing for another.
    """
    document = scan_document("HALLORAN MECHANICAL LTD INVOICE 2026-0417", ["Total due: 4,218.50"])
    chunk = next(c for c in chunker.chunk_document(document, "doc", "sha") if c.kind == "prose")
    assert chunk.headings == ["HALLORAN MECHANICAL LTD INVOICE 2026-0417"]
    assert chunk.text.startswith("HALLORAN MECHANICAL LTD INVOICE 2026-0417")


# ---------------------------------------------------------------- the measured post-condition (RET-CHUNK-001)

import math


def measured_counter(texts):
    """A stand-in for the served tokenizer: about four characters per token, deterministically.

    The real number comes from TEI's `/tokenize` on bge-m3. What these tests pin is not the tokenizer but the
    enforcement: the measurement happens after the heading prefix is in place, on every emission path, and a
    chunk that measures over the budget is split rather than emitted.
    """
    return [max(1, math.ceil(len(text) / 4)) for text in texts]


def unbroken_document(length: int = 40_000) -> dict:
    """The 756 KB shape, scaled: no sentence-ending punctuation and no whitespace anywhere."""
    run = "abc123" * (length // 6)
    return {
        "pages": {"1": {}},
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [{"self_ref": "#/texts/0", "label": "paragraph", "text": run,
                   "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 700, "b": 660}}]}],
        "tables": [],
    }


def big_table_document(rows: int = 80) -> dict:
    """A fee schedule far past the budget, with a heading so the prefix can be checked on every part."""
    cells = []
    for row in range(rows):
        for col, value in enumerate((f"Item {row} with a description of the fee", "per mile",
                                     f"{row}.{row:02d} and some words")):
            cells.append({
                "text": value,
                "start_row_offset_idx": row,
                "start_col_offset_idx": col,
                "bbox": {"l": 50 + col * 120, "t": 700 - row * 10, "r": 160 + col * 120,
                         "b": 692 - row * 10},
            })
    return {
        "pages": {"1": {}},
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {"self_ref": "#/texts/0", "label": "title", "text": "Fee schedule",
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 760, "b": 740}}]},
            {"self_ref": "#/texts/1", "label": "paragraph", "text": "This schedule lists the fees.",
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 735, "b": 730}}]},
        ],
        "tables": [{
            "self_ref": "#/tables/0",
            "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 730, "r": 470, "b": 0}}],
            "data": {"num_rows": rows, "num_cols": 3, "table_cells": cells},
        }],
    }


def test_a_run_with_no_sentence_boundary_is_measured_and_cut():
    """The 756 KB case: one word, no punctuation, so the sentence splitter and the estimator both see nothing."""
    chunks = chunker.chunk_document(unbroken_document(), "doc", "sha", count_tokens=measured_counter)
    assert len(chunks) > 1, "a 40 KB unbroken run has to become more than one chunk"
    for chunk in chunks:
        assert measured_counter([chunk.text])[0] <= chunker.MAX_TOKENS, "a chunk measured over the budget"
        assert chunk.page_start == chunk.page_end == 1
        assert chunk.token_count == measured_counter([chunk.text])[0]


def test_an_oversized_table_becomes_row_groups_that_keep_its_identity():
    """The 186 KB case: a table too large to embed is row groups, not one unembeddable entry.

    The citation needs the schedule and the row, which is why `table_id`, the total row count and each
    group's row range travel on every part, and why the heading prefix is repeated on each part.
    """
    document = big_table_document(rows=80)
    chunks = chunker.chunk_document(document, "doc", "sha", count_tokens=measured_counter)
    tables = [chunk for chunk in chunks if chunk.kind == "table"]
    assert len(tables) > 1, "an oversized table must not stay one chunk"
    ids = {chunk.table_id for chunk in tables}
    assert len(ids) == 1 and None not in ids, "every group has to keep the table's identity"
    assert all(chunk.table_rows == 79 for chunk in tables)
    assert tables[0].row_start == 0 and tables[-1].row_end == 78
    for previous, following in zip(tables, tables[1:]):
        assert following.row_start == previous.row_end + 1, "row ranges have to be contiguous and complete"
    for chunk in tables:
        assert chunk.text.startswith("Fee schedule"), "the heading prefix has to repeat on every part"
        assert measured_counter([chunk.text])[0] <= chunker.MAX_TOKENS
        assert chunk.page_start == chunk.page_end == 1


def test_a_table_that_fits_is_still_one_citation_unit():
    """The rule that must survive: a table that fits stays one whole-table entry with no row range."""
    document = big_table_document(rows=3)
    chunks = chunker.chunk_document(document, "doc", "sha", count_tokens=measured_counter)
    tables = [chunk for chunk in chunks if chunk.kind == "table"]
    assert len(tables) == 1
    assert tables[0].row_start is None and tables[0].row_end is None
    assert tables[0].text.startswith("Fee schedule")


def test_the_measurement_happens_after_the_heading_prefix():
    """The prefix is part of the embedded text, so it is part of the budget.

    The estimator sees a body with no whitespace as one token, so only the measured post-condition can catch a
    chunk whose body fits and whose prefix pushes it over.
    """
    body = "x" * 2040                                       # 510 tokens at the fake ratio
    document = {
        "pages": {"1": {}},
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {"self_ref": "#/texts/0", "label": "title", "text": "A heading path that costs tokens",
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 760, "b": 740}}]},
            {"self_ref": "#/texts/1", "label": "paragraph", "text": body,
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 700, "b": 600}}]},
        ],
        "tables": [],
    }
    chunks = chunker.chunk_document(document, "doc", "sha", count_tokens=measured_counter)
    assert len([c for c in chunks if c.kind == "prose"]) > 1, "the prefix has to count against the budget"


def test_the_last_rung_cuts_by_characters_and_still_measures_inside():
    pieces = chunker.split_to_limit("z" * 5000, lambda one: measured_counter([one])[0], chunker.MAX_TOKENS)
    assert len(pieces) > 1
    for piece in pieces:
        assert measured_counter([piece])[0] <= chunker.MAX_TOKENS


def test_sentence_boundaries_are_preferred_when_they_exist():
    text = "The rate is 67 cents per mile. " * 80
    pieces = chunker.split_to_limit(text, lambda one: measured_counter([one])[0], chunker.MAX_TOKENS)
    assert len(pieces) > 1
    for piece in pieces[:-1]:
        assert piece.rstrip().endswith("."), f"split mid-sentence: {piece[-40:]!r}"


def test_the_character_cut_verifies_each_slice_against_the_limit():
    """The ratio is an average. At the boundary it can be wrong, and the real corpus produced pieces of 518 to
    578 tokens from a run whose density changed inside the window; the cut has to measure each slice."""
    def density_changes(text: str) -> int:
        # The first 400 characters are expensive and the rest are nearly free, which is exactly the shape a
        # single averaged ratio cannot bound.
        if len(text) <= 400:
            return len(text)
        return 400 + (len(text) - 400) // 20

    pieces = chunker.split_to_limit("z" * 6000, density_changes, chunker.MAX_TOKENS)
    assert len(pieces) > 1
    for piece in pieces:
        assert density_changes(piece) <= chunker.MAX_TOKENS


# ---------------------------------------------------------------- the giant-table policy (chunker-v4)

def big_table_document_rows(rows: int) -> dict:
    """The same fixture as big_table_document, with the name the policy tests want."""
    return big_table_document(rows=rows)


def test_a_data_dump_table_has_no_row_entries_and_is_capped():
    """Above the row cap a table is a data set inside a document: bounded groups, no row entries, and a marker
    that says what is not searchable rather than dropping it silently."""
    document = big_table_document_rows(6_000)
    chunks = chunker.chunk_document(document, "doc", "sha", count_tokens=measured_counter)
    row_chunks = [chunk for chunk in chunks if chunk.kind == "table_row"]
    table_chunks = [chunk for chunk in chunks if chunk.kind == "table"]
    markers = [chunk for chunk in chunks if chunk.kind == "table_marker"]

    assert row_chunks == [], "a table over the row cap must not emit one entry per row"
    assert len(table_chunks) <= chunker.MAX_TABLE_CHUNKS
    assert len(markers) == 1, "the omitted rows have to be declared, not dropped"
    marker = markers[0]
    assert "5999 rows" in marker.text, marker.text[:200]
    assert marker.row_start == table_chunks[-1].row_end + 1
    assert marker.row_end == 5998
    for chunk in table_chunks:
        assert measured_counter([chunk.text])[0] <= chunker.MAX_TOKENS


def test_an_ordinary_table_keeps_its_row_entries():
    """The rule must not touch the tables a business actually searches."""
    document = big_table_document_rows(80)
    chunks = chunker.chunk_document(document, "doc", "sha", count_tokens=measured_counter)
    assert len([chunk for chunk in chunks if chunk.kind == "table_row"]) == 79
    assert len([chunk for chunk in chunks if chunk.kind == "table_marker"]) == 0


def test_a_large_data_file_is_classified_as_a_dataset(tmp_path):
    """The classification is type and size, made before parsing, so the expensive path is never entered."""
    big = tmp_path / "ledger.csv"
    big.write_bytes(b"a,b,c\n" * 400_000)
    small = tmp_path / "policy.csv"
    small.write_bytes(b"a,b,c\n" * 100)
    assert chunker.dataset_reason(big), "a multi-megabyte spreadsheet is a data set"
    assert chunker.dataset_reason(small) is None, "an ordinary spreadsheet is a document"

    card = chunker.dataset_chunk(relpath="company/ledger.csv", source_sha256="sha",
                                 size=big.stat().st_size, reason=chunker.dataset_reason(big))
    assert card.kind == "dataset"
    assert card.doc_id == "company/ledger.csv"
    assert "Data set" in card.text and "ledger.csv" in card.text
    assert card.page_start == card.page_end == 1
