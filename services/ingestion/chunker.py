"""Turn a parsed document into retrievable chunks, with the page and the table kept intact.

Why this is its own module rather than a step inside a worker: it is the only place where a decision
made now, about what a citation points at, becomes permanent. Everything downstream inherits it, and a
chunk that spans two pages or carries half a fee schedule cannot be fixed by the retriever or the
verifier afterwards.

The rules, from `docs/search-architecture.md` stage 2.

  * Page-bounded and structure-aware, 384 to 512 tokens, with overlap of zero to 32 tokens and zero
    preferred. Overlap makes two chunks claim the same sentence, which reads as corroboration in a
    citation panel and is not.
  * Every chunk stores a page RANGE, never a single page, because a table that spans a page break stays
    one unit.
  * A table that fits is never split. A table spanning a page break stays one retrievable unit carrying
    a `table_id`, and its rows are excluded from the surrounding page chunks.
  * Each table also gets one entry per row, carrying that row's own page and the header names, alongside
    the whole-table entry. The whole-table entry is the citation unit; the row entries are retrieval
    units, so a row on page 8 of a six-page schedule does not cite pages 7 to 9.

Two things this module does that the plan did not anticipate, both found by measuring the parser in
`docs/runbook.md` step 3.4.

  * **A table can arrive already split.** The parser emits one table per column region, so the single
    fee schedule in the fixture arrives as two tables with adjacent bounding boxes on one page. Joining
    them before the splitting rule applies is this module's job, and `join_column_splits` is where.
  * **A continuation block has no header row.** The second fragment of a split table starts at its own
    first data row, so the header names have to come from the fragment that carried them, or the
    serialised rows lose their column labels.

Token counts here are estimated from word count rather than measured with the embedding model's
tokenizer. That is deliberate for this step and is a known error, bounded by `TOKENS_PER_WORD`: the
budget is a character count derived from it, and swapping in a real tokenizer changes the constants
without changing the algorithm. The estimate is only used to bound chunk size, never to assert one.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

# The budget, in tokens, from the architecture. 384 is the floor rather than a target: a page whose
# prose runs short produces a short chunk, and padding it to reach 384 would mean inventing text.
MIN_TOKENS = 384
MAX_TOKENS = 512
MAX_OVERLAP_TOKENS = 32
PREFERRED_OVERLAP_TOKENS = 0

# Words to tokens. English prose runs about 1.3 tokens per word for the byte-pair vocabularies these
# models use, and a table cell runs higher because of the digits and punctuation. Named so it can be
# replaced rather than buried.
TOKENS_PER_WORD = 1.35

# Bumped when any rule above changes, because it is part of every chunk id: a chunker change makes new
# ids for the same document, which is what forces a deliberate re-embed rather than a silent mix of
# two chunkings in one collection.
CHUNKER_VERSION = "chunker-v2"

_WORD = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    """An estimate, documented as one. Never used to assert a size, only to bound it."""
    return max(1, round(len(_WORD.findall(text)) * TOKENS_PER_WORD))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Chunk:
    """One retrievable unit and everything a citation needs to point at it."""

    text: str
    page_start: int
    page_end: int
    kind: str                     # "prose" | "table" | "table_row"
    doc_id: str
    source_sha256: str
    ordinal: int = 0
    table_id: str | None = None
    row_index: int | None = None
    bbox_start: dict | None = None
    bbox_end: dict | None = None
    coord_origin: str | None = None
    is_ocr: bool = True
    headings: list[str] = field(default_factory=list)
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    page_is_nominal: bool = False
    chunk_id: str = ""

    def sealed(self) -> "Chunk":
        """Give the chunk its id and its neighbours' ids, once the list is complete.

        The id covers the content and the rules that produced it, so a chunker change makes new ids,
        and the same document chunked twice under the same rules makes the same ids. That is what lets
        a re-run update in place instead of duplicating a collection.
        """
        self.chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join((
            self.doc_id, self.source_sha256, CHUNKER_VERSION, str(self.ordinal),
            content_hash(self.text)))))
        return self

    def as_payload(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "source_sha256": self.source_sha256,
            "chunker_version": CHUNKER_VERSION,
            "text": self.text,
            "kind": self.kind,
            "page_start": self.page_start,
            "page_end": self.page_end,
            # True when the source format has no pages, so the page number is nominal rather than
            # measured. A citation panel should not present page 1 of an HTML file the way it presents
            # page 1 of a 90-page filing.
            "page_is_nominal": self.page_is_nominal,
            "table_id": self.table_id,
            "row_index": self.row_index,
            "bbox_start": self.bbox_start,
            "bbox_end": self.bbox_end,
            "coord_origin": self.coord_origin,
            "is_ocr": self.is_ocr,
            "headings": self.headings,
            "ordinal": self.ordinal,
            "prev_chunk_id": self.prev_chunk_id,
            "next_chunk_id": self.next_chunk_id,
            "token_estimate": estimate_tokens(self.text),
        }


# --------------------------------------------------------------------------- tables

@dataclass
class TableFragment:
    """One table as the parser emitted it, which may be part of a larger logical table."""

    self_ref: str
    page_no: int
    bbox: dict
    num_rows: int
    num_cols: int
    cells: list[dict]

    def top_row_index(self) -> int | None:
        """Which row is at the top of this fragment.

        Row indices are the parser's own sequence rather than a spatial statement, so the header is not
        reliably row zero: under a shuffled input assuming that read the header from a data row and lost
        the column names silently.

        Deciding by position needs to know which way is up, and three attempts at this got it wrong by
        assuming instead. `t` and `b` are not consistently ordered between the fragment box and its cell
        boxes on the real fixture, and the coordinate origin is a property the parser reports rather than
        a property I can rely on. So the direction is derived from the fragment's own extent, which is
        orientation-agnostic: the topmost row is the one whose cells sit further towards whichever end
        the fragment's box puts first. Row index is the tiebreak, because it is at worst an ordering.
        """
        rows: dict[int, list[float]] = {}
        for cell in self.cells:
            box = cell.get("bbox") or {}
            if "t" not in box or "b" not in box:
                continue
            rows.setdefault(cell["row"], []).append(min(box["t"], box["b"]))
        if not rows:
            return None
        lows = [min(box["t"], box["b"]) for box in (self.bbox,) if "t" in box and "b" in box]
        highs = [max(box["t"], box["b"]) for box in (self.bbox,) if "t" in box and "b" in box]
        if not lows:
            return min(rows)
        # Whichever end of the fragment the row sits nearer is the top, and if the row centres do not
        # separate cleanly the parser's own ordering decides.
        fragment_low, fragment_high = lows[0], highs[0]
        midpoint = (fragment_low + fragment_high) / 2
        upper = [index for index, values in rows.items() if sum(values) / len(values) >= midpoint]
        lower = [index for index, values in rows.items() if index not in upper]
        if len(upper) == len(rows):
            return min(rows)
        if not upper:
            return min(rows)
        if not lower:
            return min(rows)
        return min(upper)

    def row_values(self, index: int) -> list[str]:
        """One row's cell texts in reading order, which is column order."""
        cells = sorted((c for c in self.cells if c["row"] == index), key=lambda c: c["col"])
        return [c["text"].strip() for c in cells]

    @property
    def header_row(self) -> list[str]:
        """The topmost row's texts, whatever index the parser gave it."""
        index = self.top_row_index()
        return self.row_values(index) if index is not None else []

    def row_texts(self, headers: list[str] | None, skip_first: bool) -> list[tuple[int, str, dict]]:
        """Each row as one line, with its own page and bounding box.

        Row-wise rather than a grid, and with the header names folded into every row, because a row has
        to make sense when it is retrieved alone and cited alone.

        `skip_first` drops the fragment's topmost row, and the caller passes it only for fragments
        whose top row is a header. Two earlier versions got this wrong in ways that were silent: passing
        it for every fragment after the first deleted a real fee row, because the second fragment of a
        split table starts at its own first DATA row; and treating row index zero as the top of the
        table lost the header names entirely when the parser's ordering did not match the page.
        """
        rows: dict[int, list[dict]] = {}
        for cell in self.cells:
            rows.setdefault(cell["row"], []).append(cell)
        top = self.top_row_index()
        out: list[tuple[int, str, dict]] = []
        for index in sorted(rows):
            if skip_first and index == top:
                continue
            # Sorted by column index, and the names are paired in that order rather than by looking the
            # cell up by its own column number. Two attempts got this wrong, both only under a shuffled
            # input, because the parser happens to emit cells in reading order and hides the mistake:
            #
            #   * pairing names to cells positionally without sorting first produced "per callout: Item";
            #   * looking the name up by the cell's column index produced a rotated row, because the
            #     second fragment of a split table does not share the first fragment's column numbering.
            #
            # A row's cells sorted by column number ARE reading order, so position is the reliable key
            # and the column index is only a sort key.
            cells = sorted(rows[index], key=lambda c: c["col"])
            names = headers or []
            parts = [c["text"].strip() for c in cells]
            if names and len(names) == len(parts):
                line = "; ".join(f"{name}: {value}" for name, value in zip(names, parts) if value)
            else:
                line = " | ".join(part for part in parts if part)
            boxes = [c["bbox"] for c in cells if c["bbox"]]
            bbox = _union(boxes) if boxes else cells[0]["bbox"]
            out.append((index, line, bbox))
        return out


def _union(boxes: list[dict]) -> dict:
    """The smallest box containing all of them. Vertical extent takes the extremes rather than trusting
    an orientation, the same defensive choice the probes make, because a box that draws upside down is
    silent."""
    return {
        "l": min(b["l"] for b in boxes),
        "r": max(b["r"] for b in boxes),
        "t": min(min(b["t"], b["b"]) for b in boxes),
        "b": max(max(b["t"], b["b"]) for b in boxes),
    }


def find_headers(group: list[TableFragment]) -> tuple[list[str], set[int]]:
    """Which fragments carry the header row, and what it says.

    A fragment that continues a split table starts at its own first data row, so it has no header of its
    own. The way to tell is repetition: a header row is the same row of names in every fragment that has
    one, while a data row differs between fragments. So the header is the first row shared by the most
    fragments, and a fragment whose first row is that row is treated as carrying a header.

    The earlier version of this asked whether a fragment's first row "looked like" a header by word
    overlap with another fragment's first row, which is circular: in a group of one it always answered
    yes, so the fixture's own single table would have lost its header to no purpose.
    """
    tally: dict[tuple[str, ...], list[int]] = {}
    for index, fragment in enumerate(group):
        row = tuple(cell.strip() for cell in fragment.header_row if cell.strip())
        if row:
            tally.setdefault(row, []).append(index)
    if not tally:
        return [], set()
    header, carriers = max(tally.items(), key=lambda item: (len(item[1]), item[0]))
    return list(header), set(carriers)


def join_column_splits(fragments: list[TableFragment], tolerance: float = 24.0) -> list[list[TableFragment]]:
    """Group fragments that are one logical table the parser split.

    Found by measurement, `docs/runbook.md` step 3.4: a table longer than its column is emitted as one
    table per column region, with adjacent horizontal extents on the same page. The fixture's single fee
    schedule arrives as two fragments whose cells share x 53 to 297 and 315 to 559.

    Grouping is by page and by horizontal adjacency, using the widest cell extent in each fragment
    rather than the fragment's own box, because a fragment's box can be drawn tight to its text while
    the cells extend further. Ordering is left to right, which is reading order for a split like this.
    """
    by_page: dict[int, list[TableFragment]] = {}
    for fragment in fragments:
        by_page.setdefault(fragment.page_no, []).append(fragment)
    groups: list[list[TableFragment]] = []
    for page_no in sorted(by_page):
        remaining = sorted(by_page[page_no], key=lambda f: _extent(f)[0])
        current: list[TableFragment] = []
        for fragment in remaining:
            if not current:
                current = [fragment]
                continue
            _, right = _extent(current[-1])
            left, _ = _extent(fragment)
            if left - right <= tolerance:
                current.append(fragment)
            else:
                groups.append(current)
                current = [fragment]
        if current:
            groups.append(current)
    return groups


def _extent(fragment: TableFragment) -> tuple[float, float]:
    boxes = [c["bbox"] for c in fragment.cells if c["bbox"]]
    if not boxes:
        return fragment.bbox["l"], fragment.bbox["r"]
    return min(b["l"] for b in boxes), max(b["r"] for b in boxes)


def serialise_table(group: list[TableFragment], headings: list[str], caption: str = "") \
        -> tuple[str, list[str], list[tuple[int, str, dict]]]:
    """A logical table as text: a deterministic prefix, then one line per row.

    The prefix carries the heading path and the caption because the research measured a deterministic
    prefix plus table caption beating generated keywords and summaries on page accuracy, 67.38 against
    46.10 and 62.41. It costs nothing and it is embedded with the chunk.

    Returns the whole-table text, the column names it folded into the rows, and the rows with their own
    pages. The column names come back because the caller needs them to tell the header row apart from a
    data row when it decides which rows become retrievable entries.
    """
    headers, carriers = find_headers(group)
    lines: list[str] = []
    if headings:
        lines.append(" > ".join(headings))
    if caption:
        lines.append(caption)
    rows: list[tuple[int, str, dict]] = []
    for fragment_index, fragment in enumerate(group):
        body = fragment.row_texts(headers, skip_first=(fragment_index in carriers))
        rows.extend((fragment.page_no, text, bbox) for _, text, bbox in body)
    lines.extend(text for _, text, _ in rows)
    return "\n".join(lines), headers, rows


# --------------------------------------------------------------------------- prose

def has_no_pages(document: dict) -> bool:
    """Whether the parser found no page structure, which is the case for HTML, Markdown and text.

    Kept as its own question because the answer changes what a citation means rather than only how a
    chunk is built: page 1 of a document with no pages is nominal, and a citation panel should not present
    it the way it presents page 1 of a 90-page filing.
    """
    return not (document.get("pages") or {})


def page_texts(document: dict) -> list[tuple[int, str, dict, list[str]]]:
    """The document's prose in reading order, one entry per text item, with its page and headings.

    Table content is excluded, which the architecture requires: a table's rows belong to the table's own
    chunk, and letting them also sit inside a page chunk would put the same figures in two retrievable
    places with two different citations.
    """
    tables_in_flow = {
        (child.get("$ref") or "")
        for child in (document.get("body") or {}).get("children") or []
        if "tables" in (child.get("$ref") or "")
    }
    by_ref = {t.get("self_ref"): t for t in document.get("tables") or []}
    body = document.get("body") or {}
    items: list[tuple[int, str, dict, list[str]]] = []
    headings: list[str] = []
    order = _walk_order(body, document)

    # The body's children ARE the reading order, so a text item not referenced from there is invisible
    # however much content it holds. This was written against the wrong diagnosis of the lost exhibits,
    # which turned out to have a complete body and no pages rather than an empty body. It is kept because
    # the failure it guards against is real and silent: an unlinked document would produce an empty result
    # that looks like success, and an empty document is worse than a failed one. It announces itself so a
    # genuinely broken traversal is visible rather than quiet.
    if not order and (document.get("texts") or []):
        order = [t.get("self_ref") for t in document.get("texts") or [] if t.get("self_ref")]
        print(f"  warning: the document body references no items, so reading order is unavailable. "
              f"Falling back to {len(order)} text items sorted by page and position.")

    for ref in order:
        if ref in tables_in_flow or ref in by_ref:
            continue
        item = _resolve(ref, document)
        if item is None:
            continue
        label = item.get("label") or ""
        text = (item.get("text") or "").strip()
        prov = (item.get("prov") or [{}])[0]
        page_no = prov.get("page_no")
        if label in ("section_header", "title"):
            level = item.get("level") or 1
            headings = headings[: max(0, level - 1)] + [text]
            continue
        if not text:
            continue
        # A document with no page structure at all. The first real corpus run lost two HTML contract
        # exhibits this way: they parse to 158 text items with no page and no bounding box, because HTML
        # has no pages, and this function dropped every one of them for having no page number. The page is
        # the citation unit, so a document without pages cannot be cited precisely, but dropping it is the
        # one outcome that cannot be explained to a client: the text is there and the answer says nothing.
        # The whole document becomes page 1, which is coarse and honest, and the payload records that the
        # page is nominal rather than measured.
        if page_no is None:
            items.append((1, text, {}, list(headings)))
            continue
        items.append((int(page_no), text, prov.get("bbox") or {}, list(headings)))
    # Deliberately NOT re-sorted by position. The body walk returns reading order, and on a two-column
    # page a sort by vertical position is not reading order at all: it interleaves the columns. The
    # fallback path takes the parser's item order in page order, which is the least wrong available
    # answer when the traversal has failed, and it says so when it happens.
    return items


def _walk_order(body: dict, document: dict) -> list[str]:
    """Body children in order, descending into groups.

    Reading order matters here for a reason beyond tidiness: the architecture's ordering decision is
    that a chunk's text is a contiguous run of the document, so a citation can quote it.
    """
    order: list[str] = []
    for child in body.get("children") or []:
        ref = child.get("$ref")
        if not ref:
            continue
        order.append(ref)
        item = _resolve(ref, document)
        if item and (item.get("children") or []):
            order.extend(_walk_order(item, document))
    return order


def _resolve(ref: str, document: dict) -> dict | None:
    """`#/texts/3` into the document tree. A small resolver rather than a dependency."""
    if not ref.startswith("#/"):
        return None
    node: Any = document
    for part in ref[2:].split("/"):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node if isinstance(node, dict) else None


# --------------------------------------------------------------------------- the entry point

def chunk_document(document: dict, doc_id: str, source_sha256: str,
                   include_row_entries: bool = True) -> list[Chunk]:
    """A parsed document into chunks, in document order, with tables joined before they are split."""
    fragments = [
        TableFragment(
            self_ref=table.get("self_ref") or "",
            page_no=int((table.get("prov") or [{}])[0].get("page_no") or 1),
            bbox=(table.get("prov") or [{}])[0].get("bbox") or {"l": 0, "r": 0, "t": 0, "b": 0},
            num_rows=int((table.get("data") or {}).get("num_rows") or 0),
            num_cols=int((table.get("data") or {}).get("num_cols") or 0),
            cells=[
                {
                    "text": cell.get("text") or "",
                    "row": int(cell.get("start_row_offset_idx") or 0),
                    "col": int(cell.get("start_col_offset_idx") or 0),
                    "bbox": cell.get("bbox") or {},
                }
                for cell in (table.get("data") or {}).get("table_cells") or []
            ],
        )
        for table in document.get("tables") or []
    ]
    # A fragment with no cells is a parser artefact, not a table.
    fragments = [f for f in fragments if f.cells]

    headings_by_page: dict[int, list[str]] = {}
    for page_no, _, _, headings in page_texts(document):
        if page_no not in headings_by_page and headings:
            headings_by_page[page_no] = headings

    chunks: list[Chunk] = []
    for group in join_column_splits(fragments):
        first_page = group[0].page_no
        last_page = group[-1].page_no
        headings = headings_by_page.get(first_page, [])
        whole_text, column_names, rows = serialise_table(group, headings)
        table_id = f"{doc_id}:table:{group[0].self_ref.split('/')[-1]}"
        span = {p for p in range(min(first_page, last_page), max(first_page, last_page) + 1)}
        chunks.append(Chunk(
            text=whole_text, page_start=min(span), page_end=max(span), kind="table",
            doc_id=doc_id, source_sha256=source_sha256, table_id=table_id,
            bbox_start=group[0].bbox, bbox_end=group[-1].bbox,
            coord_origin=group[0].bbox.get("coord_origin"), headings=headings,
        ))
        if include_row_entries:
            header_text = " | ".join(column_names).strip()
            for ordinal, (page_no, text, bbox) in enumerate(rows):
                if not text.strip():
                    continue
                if column_names and text.strip() == header_text and len(rows) > 1:
                    # The header row on its own is a list of column names, not a retrievable row: it
                    # carries no value to check. It stays inside the whole-table entry, which is where
                    # the column names belong and where a citation to the schedule's shape points.
                    continue
                chunks.append(Chunk(
                    text=text, page_start=page_no, page_end=page_no, kind="table_row",
                    doc_id=doc_id, source_sha256=source_sha256, table_id=table_id,
                    row_index=ordinal, bbox_start=bbox, bbox_end=bbox,
                    coord_origin=bbox.get("coord_origin"), headings=headings,
                ))

    nominal = has_no_pages(document)
    for page_no, text, bbox, headings in _group_prose(page_texts(document)):
        chunks.append(Chunk(
            text=text, page_start=page_no, page_end=page_no, kind="prose",
            doc_id=doc_id, source_sha256=source_sha256,
            bbox_start=bbox, bbox_end=bbox, coord_origin=bbox.get("coord_origin"),
            headings=headings, page_is_nominal=nominal,
        ))

    for ordinal, chunk in enumerate(chunks):
        chunk.ordinal = ordinal
        chunk.sealed()
    for index, chunk in enumerate(chunks):
        chunk.prev_chunk_id = chunks[index - 1].chunk_id if index else None
        chunk.next_chunk_id = chunks[index + 1].chunk_id if index + 1 < len(chunks) else None
    return chunks


def split_oversized(text: str, page_no: int, bbox: dict) -> list[str]:
    """A passage too large for the budget, cut at sentence boundaries.

    Why a hard ceiling rather than a preference. Three constraints, all of them engineering rather than
    taste: the embedding model takes at most 8,192 tokens and a chunk past that cannot be embedded at
    all; the reranker costs 2.5 to 3 ms per token of candidate text, measured in step 3.3, so a chunk of
    800 tokens costs two and a half seconds of a five-second budget; and the prompt has to hold several
    chunks, so an unbounded one crowds out the question.

    What the ceiling is NOT is a measured quality optimum. The chunking research says plainly that there
    is no universal size and that published optima span 200 to 1,024 tokens, and it calls the
    page-versus-token accuracy difference noise. So 384 to 512 is a working range chosen to keep chunks
    embeddable, affordable to rerank and specific enough to match a question, not a law.

    Sentences rather than characters, because a citation that quotes half a sentence reads as a
    misquote.

    **It used to take a `headings` argument and never read it.** The heading is prepended once by the caller,
    to the chunk as a whole rather than to each piece of it, so the parameter was dead. It is removed rather
    than kept: a parameter that looks like it preserves something and does nothing with it is the shape of the
    defect this change exists to fix, and leaving one behind for tidiness would be leaving the pattern in.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    buffer: list[str] = []
    for sentence in sentences:
        candidate = " ".join(buffer + [sentence])
        if buffer and estimate_tokens(candidate) > MAX_TOKENS:
            pieces.append(" ".join(buffer))
            buffer = [sentence]
        else:
            buffer.append(sentence)
    if buffer:
        pieces.append(" ".join(buffer))
    return pieces


def _group_prose(items: list[tuple[int, str, dict, list[str]]]) -> Iterable[tuple[int, str, dict, list[str]]]:
    """Prose into page-bounded chunks, none larger than the budget.

    A page is never crossed, because the page is the citation unit. A page whose prose exceeds the
    budget becomes several chunks; a page that runs short stays short, which is the honest outcome of
    preferring the page boundary to the size target.

    A single item larger than the whole budget is split rather than appended. The earlier version tested
    only whether adding the NEXT item would overflow, so an item that was already too big went in whole
    and the chunk came out over the ceiling. On a dense page that is exactly the failure the ceiling
    exists to prevent: something the next stage has to reject.
    """
    if not items:
        return
    current_page = items[0][0]
    buffer: list[str] = []
    headings = items[0][3]
    first_bbox = items[0][2]

    def emit() -> tuple[int, str, dict, list[str]]:
        """The buffered prose, with the heading path in front of it.

        **The heading is content, and this is where that was wrong.** `page_texts` treats an item docling
        labels `title` or `section_header` as structure: it appends the text to a breadcrumb and does not let
        it become a content item. The breadcrumb then travels into the chunk's `headings` field, and **neither
        retrieval arm reads that field** -- the embedding is of `chunk.text` and the lexical index is of
        `payload["text"]` -- so the heading was preserved exactly where nothing could find it.

        Measured on a scanned invoice whose title line is `HALLORAN MECHANICAL LTD INVOICE 2026-0417`: the OCR
        was perfect, the payload carried the line, and `chunk_index` held zero rows matching either `HALLORAN`
        or `2026-0417` against four matching the amount that happened to sit in the body. **The invoice could
        not be found by its own number.** The same applied to every section header in every document.

        **The table path already did this**, which is what makes it an inconsistency rather than a choice:
        `serialise_table` prepends the same `" > ".join(headings)` and its docstring records why, citing the
        research that a deterministic prefix plus caption beat generated keywords on page accuracy, 67.38
        against 46.10. Prose simply never got it.

        One prefix per chunk rather than one per item, because the heading in force is the one where the
        chunk opens: a chunk that runs past a new section belongs to the section it started in.
        """
        # bbox_start is the first item's box and bbox_end the last, which is what a citation needs: the
        # span on the page, not a box drawn around the union of everything.
        body = "\n\n".join(buffer)
        prefix = " > ".join(headings).strip()
        return current_page, (f"{prefix}\n{body}" if prefix else body), first_bbox, headings

    for page_no, text, bbox, item_headings in items:
        if page_no != current_page:
            if buffer:
                yield emit()
            buffer, current_page, first_bbox, headings = [], page_no, bbox, item_headings
        elif estimate_tokens("\n\n".join(buffer + [text])) > MAX_TOKENS:
            if buffer:
                yield emit()
            buffer, first_bbox, headings = [], bbox, item_headings

        if estimate_tokens(text) > MAX_TOKENS:
            # Too big on its own, so it cannot be fixed by where the boundary falls. Split it, and do
            # not start a new chunk with the leftovers if one is already open.
            for piece in split_oversized(text, page_no, bbox):
                if estimate_tokens("\n\n".join(buffer + [piece])) > MAX_TOKENS and buffer:
                    yield emit()
                    buffer, first_bbox, headings = [], bbox, item_headings
                buffer.append(piece)
            continue
        buffer.append(text)
    if buffer:
        yield emit()
