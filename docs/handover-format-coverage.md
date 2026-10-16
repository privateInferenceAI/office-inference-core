# Handover: the format coverage gap, what was found and what is owed

**Written 2026-10-16 by the session that found it.** This is for whoever holds C2 next. It assumes you have
the repository and the deployment and nothing else.

Commits: `1c50221` (the fix), `dd8ce39` (the register). Both on `main`.

---

## 1. What was wrong

Two defects, one causing the other to be invisible.

### The pipeline refused formats it could read, silently

The worker's accepted-suffix list held **fifteen** formats. The parser reads **eighty-three extensions across
thirty-five formats**. Absent from the list were:

```
.doc .dot                    legacy Word
.xls .xlt                    legacy Excel
.ppt .pot .pps               legacy PowerPoint
.docm .dotm .dotx .xlsm .xltm .xltx .pptm .potm .potx .ppsm .ppsx
                             macro-enabled and template Office files, routine in an office
.tif .tiff .jpg .jpeg .png .bmp .gif .webp
                             every image format, which is the entire scanned-document path
.mht .mhtml .xhtml           saved web pages
.ott .ots .otp               OpenDocument templates
.epub .tex .vtt              verified working during this work
```

A file with one of those suffixes was dropped inside `discover` with **no count, no log line and no error.**
It sat in the client's folder, was absent from every search, and nothing anywhere said so. For a product whose
promise is that a client's documents are searchable, that is the one outcome nobody can discover by looking.

**It was not a missing capability.** OCR is installed in the parser and works. `libreoffice-writer` was
present, so `.doc` already parsed. The allowlist was the only thing in the way, except for one real package
gap: `libreoffice-calc` and `libreoffice-impress` were absent, so `.xls` and `.ppt` could not be opened at
all. Writer alone was a deliberate size decision (332 MB against several times that) and the consequence was
not acceptable.

### The coverage check could not have caught it

```python
covered = present & set(ACCEPTED_SUFFIXES)
missing = set(ACCEPTED_SUFFIXES) - present
print(f"suffixes covered: {len(covered)}/{len(ACCEPTED_SUFFIXES)}")
```

**The denominator is the allowlist.** It asks "does the corpus contain every format we allow" and never "do
we allow every format the parser reads". It printed `15/15 formats covered (complete)` while a third of the
parser's formats were unreachable. The list also existed **twice** -- once in `ingest.py`, once in
`fixtures/generate_corpus.py` -- and the check compared the corpus against the second copy, so two copies of
one list agreed with each other and both disagreed with reality.

**This class had already been found twice and fixed twice as instances.** `RET-FORMAT-002` records verifying
itself *by finding a format that failed it*: `.rtf` was accepted and had never once been parsed because
LibreOffice was missing. `RET-FORMAT-001` records `.eml` missing from the same list. Both were fixed. The
measurement that produced them was not.

---

## 2. What changed

### One list, loaded rather than restated

- **`services/ingestion/formats.json`** (new) -- the accepted formats and every exclusion, each with a reason
  and a decision label. This is the single source.
- **`services/ingestion/formats.py`** (new) -- loads it, exposes `ACCEPTED_SUFFIXES`, `EXCLUDED_SUFFIXES`,
  `CORPUS_MUST_COVER`, `is_accepted`, `is_excluded`.
- **`services/ingestion/ingest.py`** -- imports the list instead of defining it.
- **`fixtures/generate_corpus.py`** -- same. The duplicate tuple is gone.

**Sixty accepted, twenty-two excluded.** Each exclusion carries one of four labels and the reason says which:

| label | meaning |
|---|---|
| `TESTED` | tried it, the parser fails, here is the date |
| `UNVERIFIED` | advertised, no sample available, not promising it |
| `REFUSED PENDING A DECISION` | a real gap needing a size or cost decision, not a patch |
| `BY DESIGN` | deliberately not a document |

**Ten exclusions are `REFUSED PENDING A DECISION` and they are the finding worth your attention:** the parser
advertises `AUDIO` and `VIDEO`, this image has **no ASR model**, so a client's meeting recording or voicemail
cannot be transcribed. That is now a decision on the record rather than an absence nobody can explain.
`.msg`, `.pages` and `.key` are `UNVERIFIED` and are the cheapest next wins.

### Unreadable files are counted and reported

- `discover()` returns `(documents, unreadable)` instead of dropping silently.
- `CycleResult` gains `unreadable` and `unreadable_files`; `as_counts()` carries the count into the run record.
- The cycle prints the count and the first twenty names in the text summary, and carries both in `--json`.
- `INGESTED` / `IGNORED_SUFFIXES` in `ingest.py` keeps operating-system debris out of the count, because a
  number that includes a `.DS_Store` is a number an operator learns to ignore.

### The measurement runs in the missing direction

- **`scripts/dump_parser_formats.py`** (new) -- runs inside the parser image, writes docling's own
  `FormatToExtensions` to **`docs/parser-formats.json`** (new, committed).
- **`scripts/check_parser_formats.sh`** (new) -- asks the deployed parser what it reads, compares it against
  the committed file, and **fails on drift or on any format nobody has decided**. It spans two containers,
  which is why it is a script: a pytest run sees the parser or the repository and never both.
- **`tests/test_format_coverage.py`** (new) -- the same assertions where they can run locally, plus the
  regression guard that the formats a client actually brings are accepted.
- `coverage_report` in the corpus generator now measures against the **twenty-one formats a client brings**
  rather than all sixty, and states in its own output what it cannot prove.

### The images

- **`services/parser/Dockerfile`** -- `libreoffice-calc` and `libreoffice-impress` added, with a build
  assertion that reads LibreOffice's **component registry** (`calc.xcd`, `impress.xcd`, `writer.xcd`) rather
  than trusting package names.
- **`services/ingestion/Dockerfile`** -- copies `formats.py` and `formats.json`.

### The corpus

- **Four rendered scans** (`.tif`, `.tiff`, `.png`, `.jpg`) carrying values that appear nowhere else, so a
  question about them can only be answered through OCR.
- **Three legacy Office files** converted from the modern ones with LibreOffice, so all three are genuinely
  OLE2 rather than a guess at the format.
- Coverage went from **15 of the formats a client brings to 21 of 21**.

---

## 3. What was verified, with evidence

Everything below was measured on the reference instance, `i-096bebd463808d1673`.

**The legacy spreadsheet that could not be opened at all before:**

```
company/000030.xls   22.8s, 878,388 text nodes, 5,180,005 characters
'Table with column headers in row 7. Area name in column J.
 Estimates Geography File with Census and FIPS Codes...'
```

A US Census Bureau workbook saved in Excel 2003. It returned *"source file could not be loaded"* from
LibreOffice nine times today, before Calc was installed.

**OCR, which no format could reach before:**

```
scan-test.png   3.0s  ->  'INVOICE 2026-0417 Supplier: Halloran Mechanical
                           Amount due: 4,218.50 Boiler service, Lake Kayla site'
scan-test.tiff  1.0s  ->  identical
```

**End to end through the worker, all seven new fixtures, zero failures:**

```
seen 10033, new 7, changed 0, skipped 10026, failed 0
chunks written 21, chunks removed 0

  company/legacy-briefing.ppt   ok
  company/legacy-ledger.xls     ok
  company/legacy-memo.doc       ok
  company/scan-invoice.jpg      parse 1044ms
  company/scan-invoice.png      parse 1115ms
  company/scan-invoice.tif      parse 20687ms
  company/scan-invoice.tiff     parse 20691ms
```

**No unreadable line was printed, because nothing was unreadable.** Before the change all seven would have
been dropped in silence.

**The format checker, against the live parser:**

```
docling 2.132.0: 35 formats, 86 extensions
matches: 35 formats, 86 extensions
all decided: 60 accepted, 22 excluded with reasons
```

**The artefacts are real.** `chunk_index` holds the OCR text:

```
company/scan-invoice.png | prose | company |
  'Site: Lake Kayla pumping station Work: boiler service and pressure test
   Parts: 1,284.00 Labour: 2,610.50 Total due: 4,218.50 Authorised by: D. Okonkwo'
```

**752 tests pass, 3 skip.** The full ladder is green: six probes, both boundary paths, contract match, format
checker.

**One flake worth knowing about.** `stream_probe.py` failed once in the ladder run immediately after I
restarted the ingestion worker, then passed standalone and on the next two ladder runs. It is timing
sensitive (TTFT, frame arrival spread) and the ingest cycle contends for the embedding service. **Run the
ladder with no cycle in flight**, or expect that probe to be the one that wobbles.

---

## 4. What is owed

### `RET-HEAD-001` -- a fact that appears only in a heading is not retrievable

**Found while testing the fix. Not fixed. This is the more serious of the two defects.**

`chunker.page_texts` treats an item docling labels `title` or `section_header` as structure rather than
content:

```python
if label in ("section_header", "title"):
    headings = headings[: max(0, level - 1)] + [text]
    continue          # <- the text never becomes content
```

The line becomes a `headings` breadcrumb, which travels into the Qdrant payload's `headings` field. **Neither
retrieval arm reads that field**: the embedding is of `chunk.text` and the lexical index is of
`payload["text"]`. So the heading is preserved exactly where nothing can find it.

Measured on `company/scan-invoice.png`, whose title line is `HALLORAN MECHANICAL LTD INVOICE 2026-0417`:

```
chunk_index rows matching 'HALLORAN'                 0
chunk_index rows matching '2026-0417'                0
chunk_index rows matching '4,218.50'  (in the body)  4

Qdrant payload: headings: ['HALLORAN MECHANICAL LTD INVOICE 2026-0417']
                text:     'Site: Lake Kayla pumping station ...'
```

**A user asking for the invoice by number or by supplier gets nothing, while the document is indexed and the
OCR is perfect.** The general form is worse than the scan: any document whose identifying line is a title
loses it from search.

**The fix is a decision, not a patch.** Two options, and they are not equivalent:

1. **Include headings in the embedded text.** Fixes both arms. Changes every chunk's vector, so it needs a
   `PAYLOAD_VERSION` bump and a full re-embed -- measured at **1h36m for 10,000 documents** with the current
   concurrency.
2. **Add headings to the lexical arm only.** Fixes exact-identifier search, which is what an invoice number
   is, for a fraction of the cost. Leaves the vector arm blind to headings.

I did not choose, because either is defensible and the tradeoff is the owner's.

### Audio and video

Ten formats `REFUSED PENDING A DECISION`. The parser advertises them and this image has no ASR model --
`transformers` is present, no whisper weights are. Accepting `.mp3` or `.mp4` without the model promises a
transcription that fails. **This is the same class as the `.xls` gap**: capability advertised, dependency
missing, and the only reason it is now visible is that the exclusion list says so out loud.

### Unverified formats

`.msg` (Outlook saved messages), `.pages` and `.key` (Apple iWork), `.boxnote`, `.afp`. Advertised by the
parser, no sample available to test. `.msg` and `.pages` are worth a sample each: a small business has both.

### The coverage corpus cannot make a real scan

The four scan fixtures are **rendered**, not scanned: clean synthetic glyphs at a known size. They prove the
path works and they do not prove it works on a 1998 fax. A genuinely poor scan is a fixture this cannot make
and the corpus does not have.

---

## 5. How to check all of it yourself

```bash
# The direction that was missing. Fails on drift or on any undecided format.
bash scripts/check_parser_formats.sh

# Regenerate after an intended parser upgrade, then review the diff and decide each new format.
bash scripts/check_parser_formats.sh --write

# The unit tests, including the format coverage file.
python3 -m pytest tests/ -q

# The corpus, and what it covers.
python3 fixtures/generate_corpus.py --out /tmp/corpus-check

# The acceptance ladder. Run it with no ingest cycle in flight.
for p in api_probe.py access_probe.py activity_probe.py job_probe.py interface_probe.py stream_probe.py; do
  bash scripts/probe.sh "$p"
done
```

---

## 6. What I would look at first

1. **`RET-HEAD-001`.** It is a silent retrieval failure of the same shape as the one just fixed, it affects
   every document type rather than scans alone, and there is already a fixture that demonstrates it.
2. **The audio and video decision.** Ten excluded formats is a real answer; ten excluded formats nobody has
   read is not.
3. **`.msg` and `.pages`.** Cheapest remaining wins, and both are formats a small business actually holds.

**One thing to carry forward.** Both defects had the same shape: *a field or a list that looks like it
preserves something, and nothing reads it.* The accepted-suffix list looked like the definition of what the
pipeline supports. The `headings` field looks like the heading is kept. The coverage check looked like a test.
When you add the next one, ask what reads it.
