# How the ingestion pipeline should handle `.rtf`

Research note. No code was changed. Every claim cites the page or file it came from.

Date: 2026-10-13. Parser under test: `ghcr.io/docling-project/docling-serve-cpu:v1.36.0`, digest
`sha256:225c8586e20d5d0fc6811a9e0e044fa602bcc4393f00389009bad42d6787b58f` (the digest pinned in
`services/parser/Dockerfile`).

---

## 1. Does Docling support RTF, and why does it fail?

Docling supports RTF. The failure is a missing system package, not a missing format.

**Docling's own format list says so, and names the dependency.** The supported-formats table has the row
`RTF | Rich Text Format; requires LibreOffice`, alongside `DOC, XLS, PPT | Legacy binary Office formats
(97-2004); requires LibreOffice`
([docling-project.github.io/docling/usage/supported_formats](https://docling-project.github.io/docling/usage/supported_formats/)).
RTF is a first-class format with a stated requirement, not an unhandled extension.

**It is registered in the code.** In the installed image, `docling/datamodel/base_models.py` defines
`InputFormat.RTF = "rtf"`, and `docling/document_converter.py:384` maps it:
`InputFormat.RTF: WordFormatOption()`. So RTF is routed to the MS Word backend deliberately.

**The backend converts RTF to DOCX first, using LibreOffice.** `docling/backend/msword_backend.py:780`:

```python
if in_doc.format in {InputFormat.DOC, InputFormat.RTF}:
    path_or_stream = convert_to_modern_format(path_or_stream, in_doc.format.value, "docx")
```

`convert_to_modern_format` lives in `docling/backend/docx/drawingml/utils.py:261`. Its first act is
`get_libreoffice_cmd()` (line 292), and when that returns `None` it raises at line 294:

```
RuntimeError: LibreOffice is required to convert a .rtf file to .docx. Install LibreOffice and make
sure it is on PATH.
```

**LibreOffice is absent from the CPU image.** Confirmed by running the image:

```
$ docker run --rm --entrypoint sh ghcr.io/docling-project/docling-serve-cpu:v1.36.0 -c \
    'for t in pandoc libreoffice soffice unrtf abiword; do command -v $t || echo MISSING; done'
pandoc       MISSING
libreoffice  MISSING
soffice      MISSING
unrtf        MISSING
abiword      MISSING
```

The project's own OS package list agrees. `os-packages.txt` from the docling-serve repo contains eight
entries and no office suite: `tesseract`, `tesseract-devel`, `tesseract-langpack-eng`, `tesseract-osd`,
`leptonica-devel`, `libglvnd-glx`, `glib2`, `libatomic`
([raw.githubusercontent.com/docling-project/docling-serve/main/os-packages.txt](https://raw.githubusercontent.com/docling-project/docling-serve/main/os-packages.txt)).

**Reproduced end to end.** Running the real fixture and a hand-written well-formed RTF through Docling's
converter inside the image produced the same `RuntimeError` for both, each surfacing as
`ConversionError: Conversion failed ... with status: failure`. The identical traceback for a valid file
is the proof that the fixture's content is not the cause.

### Correction to the task brief

The parser image is **CentOS Stream 9**, not Debian-based:

```
$ docker run --rm --entrypoint sh ghcr.io/docling-project/docling-serve-cpu:v1.36.0 -c 'cat /etc/os-release'
NAME="CentOS Stream"
VERSION="9"
```

This matters for section 3: the fix package must come from CentOS repos, not `apt`.

### The fixture is also not real RTF

`fixtures/generate_corpus.py:241` builds the RTF case with faker-file's `RtfFileProvider`. That provider
writes plain text and nothing else:

```python
data = {"content": content, "filename": filename, "storage": storage}
if raw:
    raw_content = BytesValue(content.encode("utf8"))
    ...
storage.write_text(filename, content)
```

Its `extension` is `"rtf"` and the content is passed straight through with no RTF header or control
words. Requirement `faker-file[docx,xlsx,pptx,odt,ods,odp,pdf]` in `requirements-dev.txt` omits the RTF
extra with the comment that "TXT, CSV, RTF and EML are produced from the core package" from
(`faker_file/providers/rtf_file.py` in the installed package). The generated file confirms it: `grep -c
'\\rtf'` returns 0, and `file` reports `ASCII text` rather than `Rich Text Format data, version 1, ANSI`.
The docx provider by contrast builds genuine OOXML via `python-docx`.

So the corpus covers the `.rtf` suffix without covering RTF. `--out` reports `suffixes covered: 15/15
(complete)`, which is why this looked tested. A fix has to handle both a malformed file and a real
client RTF, because both will arrive.

### The failure is then classified as the document's fault

`services/ingestion/ingest.py:308` raises a bare `RuntimeError` when the parser reports
`task_status == "failure"`. `is_transient` (line 53) treats only gateway errors, timeouts and connection
failures as transient:

```python
if isinstance(exc, urllib.error.HTTPError):
    return exc.code in RETRY_STATUSES
return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))
```

`RuntimeError` is none of those, so the manifest records `failure_kind: "permanent"` and increments
`failure_count` (line 1219). At `MAX_FAILURES` (default 2, line 49) the file is skipped with
`dead-lettered after N failures, waiting for the file to change` (line 1167) and is not retried until
its bytes change. That is the observed behaviour.

Note also that the deployed manifest already holds a dead-letter record for this file keyed on its
content hash, so simply installing a converter will not re-ingest it. The file has to change or the
record has to be cleared with `clear_failures` (line 729).

---

## 2. If Docling does not support RTF, what are the options?

Docling does support RTF, so the question becomes how to satisfy the stated requirement. Options below.
"It runs offline" means no network call at conversion time; all of these qualify, since the work is a
local subprocess or a local Python call. Licences were read at the source, not recalled.

| Option | Licence (source read) | Offline | What it takes |
|---|---|---|---|
| **A. LibreOffice in the parser image** | MPL-2.0 / LGPL-3.0 ([LibreOffice COPYING.LGPL](https://raw.githubusercontent.com/LibreOffice/core/master/COPYING.LGPL), [core/README.md](https://raw.githubusercontent.com/LibreOffice/core/master/README.md): "based on copyleft licenses") | Yes, build-time install only | One line in `services/parser/Dockerfile`. No code change. |
| **B. pandoc before the parser** | GPL-2.0 ([pandoc COPYING.md](https://raw.githubusercontent.com/jgm/pandoc/main/COPYING.md)) | Yes | Pre-conversion step in the worker plus a subprocess. |
| **C. unrtf before the parser** | GPL ([Debian copyright for unrtf 0.21.10-clean-1](https://metadata.ftp-master.debian.org/changelogs//main/u/unrtf/unrtf_0.21.10-clean-1_copyright): "You are free to distribute this software under the terms of the GNU General Public Licence") | Yes | Pre-conversion step. |
| **D. striprtf in the worker** | BSD-3-Clause ([PyPI JSON `license_expression`](https://pypi.org/pypi/striprtf/json), [LICENSE](https://raw.githubusercontent.com/joshy/striprtf/master/LICENSE)) | Yes, pure Python | One pip line plus a conversion step. |
| **E. textract** | MIT ([PyPI JSON](https://pypi.org/pypi/textract/json)) | Yes, but see below | Not recommended. |
| **F. Drop `.rtf`, require `.docx`** | n/a | n/a | Delete one suffix, update three docs. |

**Option A, LibreOffice, is the only one that needs no application code.** `get_libreoffice_cmd()`
checks `DOCLING_LIBREOFFICE_CMD`, then `shutil.which("libreoffice")`, then `shutil.which("soffice")`
(`docling/backend/docx/drawingml/utils.py:198`). Docling already isolates the LibreOffice profile per
conversion and bounds it with a timeout (`_isolated_libreoffice_profile`, used at line 306), so
concurrent conversions do not collide. The parser wrapper runs as uid 1001 with `HOME=/opt/app-root/src`,
which is writable, so no permission change is needed. LibreOffice is in the image's own repos:

```
$ dnf list available libreoffice-writer
libreoffice-writer.x86_64    1:7.1.8.1-16.el9    appstream
```

Footprint, measured by installing `libreoffice-writer` with `--no-install-recommends` on Debian trixie as
a proxy: **165 new packages**, 224 MB in `/usr/lib/libreoffice` plus 12 MB in `/usr/share/libreoffice`.
`soffice --version` reported `LibreOffice 25.2.3.2`. The CentOS 9 version is 7.1.8.1, older than the one
I exercised, so the version I proved is not the version that would ship.

**Option D, striprtf, is the smallest by dependency count.** PyPI metadata for 0.0.33 lists no
`requires_dist` for the runtime (only a `dev` extra), so it is pure Python with zero dependencies, and
its wheel is 8.4 kB. It is also more robust than LibreOffice on bad input: it converted both the
malformed fixture and a well-formed RTF to sensible text. It must be applied before the parser, so it
changes the worker rather than the parser. It also degrades structure: its output is plain text, so
tables and headings are flattened and there are no page numbers to cite. On the evidence of the
ingestion design that is acceptable, because `RET-FORMAT-001` records that `.eml` "is pageless, which the
chunker already handled by giving it a nominal page", and the spreadsheet case is already table-only.

**Option B, pandoc** handles RTF as an input format but, like the others in this family, it produces
converted output rather than a page-numbered Docling document, and it is a larger install than striprtf
(six packages on Debian).

**Option C, unrtf** is one 49.5 kB package (148 kB installed) per the
[Debian package page](https://packages.debian.org/trixie/unrtf). Its own description is candid about
quality: "Converting to HTML, it supports tables, fonts, colors, embedded images, hyperlinks, paragraph
alignment among other things. All other conversions are 'alpha'--just begun." Text output, which is what
this pipeline would want, is the alpha part.

**Option E, textract, adds no capability.** Its RTF support is a shell-out to unrtf, so it is option C
with a wrapper and a large dependency tree:

```python
class Parser(ShellParser):
    """Extract text from rtf files using unrtf."""
    def extract(self, filename, **kwargs):
        stdout, stderr = self.run(["unrtf", "--text", filename])
```

([textract/parsers/rtf_parser.py](https://raw.githubusercontent.com/deanmalmgren/textract/master/textract/parsers/rtf_parser.py)).
Its dependency list includes pillow, speechrecognition, pdfminer-six, python-pptx, openpyxl, xlrd and
odfpy, and it *also* needs LibreOffice for `.doc`. It is MIT and maintained (2.1.0, 2026-09-01), so the
objection is not staleness, it is that it does not remove the unrtf dependency it exists to provide.

### A note on the GPL options

pandoc and unrtf are GPL-2.0 and GPL respectively. The stack does not link them, it would invoke them as
separate executables, which is the arrangement that usually avoids copyleft reaching the calling code.
I am flagging it rather than deciding it, because this repo has a stated position on copyleft in shipped
images (`requirements-dev.txt` rejects an LGPL route for fixtures; the compose file documents licences
it accepts). Option D is BSD-3 and option A is MPL-2.0/LGPL-3.0, so both sidestep the question entirely.

---

## 3. What is already in the images?

**Parser image, `docling-serve-cpu:v1.36.0`:** CentOS Stream 9. `pandoc`, `libreoffice`, `soffice`,
`unrtf`, `abiword`, `wvText`, `catdoc`, `antiword` and `unoconv` are all absent. Python has `docling`
2.132.0, `docling-core` 2.99.0, `docling-parse` 7.22.1, `python-docx` 1.2.0 and `olefile` 0.47, but no
RTF library. `os-packages.txt` is the authoritative list of what it adds, and it is eight packages, none
of them an office suite.

**Ingestion image:** Debian 13 (trixie), Python 3.12.15. Installed packages are `pip` and
`psycopg2-binary 2.9.13` only. `pandoc`, `libreoffice`, `soffice`, `unrtf` and `abiword` are absent.

**Could the ingestion image do the conversion instead?** For a pure-Python option, yes and cheaply.
`python:3.12-slim` has no compiler, and the Dockerfile's own comment explains that this is why
`psycopg2-binary` is pinned to a version with a published manylinux wheel. A pure-Python wheel like
striprtf fits the same rule and needs no compiler. Installing LibreOffice in the worker would be the
wrong place: it needs no parsing libraries, and the Dockerfile states the reason explicitly ("parsing is
the parser service's job"). It would also put a second office suite on the box.

---

## 4. The smallest correct fix

**Recommendation: install LibreOffice in the parser image (option A), and fix the corpus generator so
the RTF fixture is real.**

The decisive facts are that Docling already contains the entire RTF path and only lacks the executable,
and that the parser is already a local Dockerfile over the pinned upstream digest
(`services/parser/Dockerfile`, `FROM ghcr.io/docling-project/docling-serve-cpu@sha256:225c8586...`).
Adding LibreOffice is therefore a Dockerfile line with no application code change, which makes it
smaller in the sense that matters here than any option that touches the worker's control flow. It also
fixes RTF for real client files rather than only for the fixture, and it keeps the format working for
the `.doc` and `.xls` files the manual currently tells clients to re-save.

I verified the outcome rather than assuming it. Converting both files with LibreOffice to DOCX and then
parsing the results with the image's own Docling produced the text:

- real fixture: 9,986 characters recovered, starting `Station treat court simply. Pattern have radio
  song guy.`
- well-formed RTF: `Hello RTF world. This is a paragraph. / Second paragraph.`

So the converter plus the existing parser recovers content correctly for both a malformed and a valid
file.

**I would pair that with the generator fix**, because otherwise the corpus still claims 15/15 coverage
while `.rtf` is exercised only with a file that is not RTF. A real RTF fixture is what makes
`RET-FORMAT-001`-style verification meaningful for this suffix.

**If image size is the binding constraint, option D is the fallback.** striprtf is BSD-3, pure Python,
zero runtime dependencies, and it handled the malformed fixture better than LibreOffice did. It costs a
code change in the worker to convert before submitting, and it gives up page numbers, which this
pipeline tolerates elsewhere.

**On dropping `.rtf` (option F).** It is a legitimate answer and I would not argue against it on
principle: it is zero dependencies, and it is exactly the precedent the repo already set for `.doc` and
`.xls`, which are excluded *for this same reason*. `docs/manual.md:1804` states the principle plainly
for `.msg`: "adding a suffix is a promise that files with it are ingested. A client with Outlook exports
should be told that before they drop a folder of them in." By that rule `.rtf` is currently a promise
the system does not keep, and `.doc` and `.xls` being excluded while `.rtf` is accepted is inconsistent
given all three need the same missing binary.

I still recommend fixing it rather than dropping it, for one reason: the fix is a one-line Dockerfile
change to a service the project already builds, so the cost of keeping the promise is unusually low, and
a client who supplies RTF gets indexed instead of told to re-save. Dropping it is the better answer only
if the parser image must not grow.

**Whichever way it goes, the silent part deserves its own fix.** Today a missing system package and a
corrupt client document produce the same `permanent` classification and the same one-line failure count.
The parser's `RuntimeError` message already contains the reason, and it is persisted to the manifest's
`last_error`, so the information exists; it is simply not surfaced anywhere a client-facing operator
would see it. Calling a configuration defect "permanent" for the document is the same class of mistake
the code comments describe having already been fixed once for transient 504s (line 56).

### What I could not determine

- **I could not build and run a modified parser image**, because that needs package installs inside the
  container and this environment has no network access from the build. My end-to-end proof used the
  host's LibreOffice 7.3.7.2 for the conversion step and the image's own Docling for the parse. The
  conversion semantics are the same, but the in-container integration (CentOS's LibreOffice 7.1.8.1,
  running as uid 1001, under `PARSER_MAX_CONCURRENT=1` with `DOCLING_NUM_THREADS=12`) is unverified. That
  is the runtime check I would want before committing.
- **Whether one client RTF uses features where LibreOffice 7.1 and striprtf disagree.** RTF is a large
  specification and I only tested two files, one of which is not valid RTF.
- **Whether the deployed box has already dead-lettered this file**, since I have no access to its
  manifest. If it has, installing a converter alone will not re-ingest it.
- **The exact CentOS install footprint.** The 165-package and 236 MB figures are Debian trixie; CentOS
  Stream 9 will differ.
- **Whether `RET-FORMAT-001` should be extended to cover RTF or a new requirement added.** I read the
  requirement and confirmed it covers only email, spreadsheets and slides, which is a documentation
  decision rather than a technical one.
