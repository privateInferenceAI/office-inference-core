"""What a table row's text says about the table it came from.

**Why the row needs it.** Corpus 2's row reads `Total revenues | 4,934,098 | 4,136,543 | 8,947,032 |
8,183,283`. Four figures, and the labels that say which period each belongs to sit in the two header rows
above it. Before chunker-v5 those labels were reachable only by a lookup at render time, so the lexical
index and any row query saw a line of numbers with no table and no period attached.

These are unit tests over synthetic tables shaped the way the parser emits them, which is what makes the
rules pinnable: the top row is dropped as the header, a period header can be two rows, and a caption sits
above the header block. The measured claim is the probe's, not this file's; what this file proves is that
the derivation is deterministic, complete for the shapes measured, and absent where it would be a guess.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import chunker  # noqa: E402  (the import needs the path above)

# Four columns, so a cell's box is where the parser puts it on a page.
COLUMNS = [(54, 120), (130, 200), (210, 280), (290, 360)]

# The shape that was measured: two header rows, the first naming the periods and the second the years.
PERIOD_HEADER = ["Three Months Ended June 30,", "Six Months Ended June 30,", "", ""]
YEAR_HEADER = ["2026", "2025", "2026", "2025"]
TOTAL_REVENUES = ["Total revenues", "4,934,098", "4,136,543", "8,947,032"]
TOTAL_EXPENSES = ["Total expenses", "4,204,825", "3,910,120", "8,350,449"]
EXPECTED_COLUMNS = ("Columns: Three Months Ended June 30, | Six Months Ended June 30, / "
                    "2026 | 2025 | 2026 | 2025.")


def cell(text: str, row: int, col: int) -> dict:
    """One cell, with a box placed the way a page places it: row zero is highest on the page."""
    left, right = COLUMNS[col]
    top = 700 - row * 12
    return {"text": text, "start_row_offset_idx": row, "start_col_offset_idx": col,
            "bbox": {"l": left, "r": right, "t": top, "b": top - 12, "coord_origin": "BOTTOMLEFT"}}


def document(rows: list[list[str]], paragraphs: tuple[str, ...] = ()) -> dict:
    """A parsed document holding one table, plus optional prose above it."""
    texts = [
        {"self_ref": f"#/texts/{index}", "label": "paragraph", "text": text,
         "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 740, "b": 700}}]}
        for index, text in enumerate(paragraphs)
    ]
    title = {"self_ref": f"#/texts/{len(texts)}", "label": "title", "text": "Meridian", "level": 1,
             "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 760, "b": 745}}]}
    table = {
        "self_ref": "#/tables/0",
        "prov": [{"page_no": 1, "bbox": {"l": 54, "r": 360, "t": 710, "b": 400,
                                         "coord_origin": "BOTTOMLEFT"}}],
        "data": {"num_rows": len(rows), "num_cols": 4,
                 "table_cells": [cell(text, row, col)
                                 for row, values in enumerate(rows)
                                 for col, text in enumerate(values)]},
    }
    return {
        "pages": {"1": {}},
        # The title comes first, so the prose and the table both sit under it and the heading path is
        # non-empty for the table. That is what makes "the title is not in the row" a real assertion
        # rather than one that would pass on an empty heading path.
        "body": {"children": [{"$ref": title["self_ref"]}]
                            + [{"$ref": item["self_ref"]} for item in texts]
                            + [{"$ref": "#/tables/0"}]},
        "texts": texts + [title],
        "tables": [table],
    }


def chunks_with_rows(rows: list[list[str]], paragraphs: tuple[str, ...] = (), **kwargs) -> list:
    return chunker.chunk_document(document(rows, paragraphs), "doc", "sha", **kwargs)


def row_chunk(rows: list[list[str]], needle: str):
    found = [chunk for chunk in chunks_with_rows(rows)
             if chunk.kind == "table_row" and needle in chunk.text]
    assert len(found) == 1, f"expected one row holding {needle!r}, got {len(found)}"
    return found[0]


def test_a_two_row_period_header_is_prepended_to_the_row():
    """The measured fix, in the index rather than at render time. A row that says 4,934,098 has to be able
    to say which period that is, and the answer is in the two rows above it."""
    chunk = row_chunk([PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES], "4,934,098")
    assert chunk.text.startswith(EXPECTED_COLUMNS), chunk.text
    assert "Total revenues" in chunk.text
    assert chunk.table_context == EXPECTED_COLUMNS


def test_a_caption_above_the_header_block_is_included():
    """The section caption is what tells a reader which schedule they are looking at, and it sits above
    the header rather than inside the row. It is included when it is there and never invented when it is
    not: the next test covers the absent case."""
    rows = [["Profit (Loss) by Segment", "", "", ""], PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES]
    chunk = row_chunk(rows, "4,934,098")
    assert chunk.text.startswith(
        "Table: Profit (Loss) by Segment. " + EXPECTED_COLUMNS), chunk.text


def test_a_table_with_no_header_keeps_the_bare_row():
    """A header-less table is a grid of values with nothing above it that names a period. Guessing one
    would be worse than saying nothing: the row would claim a context the document does not state."""
    rows = [["Widget A", "12", "34", "56"], ["Widget B", "98", "76", "54"]]
    chunks = [chunk for chunk in chunks_with_rows(rows) if chunk.kind == "table_row"]
    assert chunks, "the table should still produce row entries"
    for chunk in chunks:
        assert not chunk.text.startswith("Columns:"), chunk.text
        assert not chunk.text.startswith("Table:"), chunk.text
        assert chunk.table_context == ""


def test_a_row_of_figures_is_not_mistaken_for_a_header():
    """The header test is a period phrase AND at most one value. A data row that happens to say
    "as of December 31" carries four figures, so it is a value row and the real header above it stays in
    force for the rows below."""
    rows = [PERIOD_HEADER, YEAR_HEADER,
            ["Adjustment as of December 31, 2026", "1,234", "5,678", "9,012"],
            TOTAL_EXPENSES]
    chunk = row_chunk(rows, "Total expenses")
    assert chunk.text.startswith(EXPECTED_COLUMNS), chunk.text


def test_prose_and_the_whole_table_entry_are_unchanged():
    """The change is to row entries only. A whole-table chunk is the citation unit and a prose chunk is
    the page's text; prefixing either would put the same context in two places and make a citation quote
    words that are not on the page."""
    rows = [PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES, TOTAL_EXPENSES]
    with_rows = chunks_with_rows(rows, paragraphs=("A note about the quarter. ",))
    without = chunks_with_rows(rows, paragraphs=("A note about the quarter. ",), include_row_entries=False)
    assert [chunk.text for chunk in with_rows if chunk.kind != "table_row"] ==            [chunk.text for chunk in without], "a non-row chunk changed when row entries were added"
    table = next(chunk for chunk in with_rows if chunk.kind == "table")
    assert "Columns:" not in table.text and "Table:" not in table.text, table.text


def test_the_document_title_and_heading_path_are_not_added_to_a_row():
    """Measured on corpus 2: the title and heading path made retrieval worse. They stay in the metadata,
    where a citation can use them, and out of the row's text."""
    chunks = chunks_with_rows([PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES],
                              ("A note about the quarter. ",))
    chunk = next(chunk for chunk in chunks
                 if chunk.kind == "table_row" and "4,934,098" in chunk.text)
    assert chunk.headings == ["Meridian"], "the heading path should still be carried as metadata"
    assert "Meridian" not in chunk.text, chunk.text


def test_row_metadata_is_populated():
    """The lexical arm gets the same facts the vector payload has: which table, which row, which range,
    how many rows in the table. Without them the columns added to chunk_index would be written empty."""
    rows = [PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES, TOTAL_EXPENSES]
    row_chunks = [chunk for chunk in chunks_with_rows(rows) if chunk.kind == "table_row"]
    assert len(row_chunks) == 3, "the dropped top row is the header, so three rows are emitted"
    for chunk in row_chunks:
        assert chunk.table_id == "doc:table:0"
        assert isinstance(chunk.row_index, int)
        assert chunk.row_start == chunk.row_index
        assert chunk.row_end == chunk.row_index
        assert chunk.table_rows == 3, "the table's own row count, not the chunk's"
    assert [chunk.row_index for chunk in row_chunks] == [0, 1, 2]


def test_the_context_is_a_function_of_the_table_alone():
    """Two runs over one document have to produce the same text, or the ids differ and a re-ingest
    duplicates the collection instead of updating it."""
    first = chunks_with_rows([PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES])
    second = chunks_with_rows([PERIOD_HEADER, YEAR_HEADER, TOTAL_REVENUES])
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]


def test_the_header_test_rejects_a_long_sentence_that_names_a_month():
    """The length rule is what keeps an ordinary sentence about May from being read as a column label."""
    sentence = ("Revenue for the period includes the seasonal peak that begins in May and runs through "
                "the summer months, which the prior year did not have, and is stated before intercompany "
                "eliminations and before any adjustment for foreign exchange movements.")
    assert len(sentence) > 220
    assert chunker.looks_like_table_header(sentence) is False


def test_the_caption_test_rejects_a_value_row_and_a_single_word():
    assert chunker.table_caption("Total revenues | 4,934,098 | 4,136,543") == ""
    assert chunker.table_caption("Revenues") == ""
    assert chunker.table_caption("Profit (Loss) by Segment") == "Profit (Loss) by Segment"
