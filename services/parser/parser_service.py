"""A document parser that returns Docling's per-page confidence instead of dropping it.

Why this exists rather than docling-serve. That service computes the per-page confidence report and then
serialises only the document-level rollup, which its own code acknowledges:

    class ConfidenceScores(BaseModel):
        '''JSON-safe snapshot of docling confidence scores.
        Document-level only. A per-page breakdown can be added later as an optional ``pages`` field...'''

The per-page data is what tells a reader which pages were read off an image, and without it the pipeline
cannot disclose that a figure came from a scan. The served rollup also hides the worst page: on the fixture
corpus's 59-page IRS publication the document reports low_score 0.9326 while page 59 scores 0.6739, because
a 5th-percentile rollup over 59 pages cannot be moved by one bad page.

So this wrapper runs the same library in the same container and returns the whole report. The wire shape is
deliberately docling-serve's, so the ingestion worker talks to either service without a code change and the
difference between them is what comes back, not how it is asked.

What this deliberately does NOT do: reproduce docling-serve's option vocabulary. Its `ocr_preset` and
`table_structure_preset` map to pipeline classes through a translation that lives in that project, and
reimplementing it from the outside would be guessing at a mapping rather than using one. This service takes
a small set of options it documents, and refuses anything else rather than ignoring it, because a silently
ignored option is this project's most expensive recurring bug.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

app = FastAPI(title="oi-core parser", docs_url=None, redoc_url=None, openapi_url=None)

def _upload_dir() -> Path:
    """Where an uploaded document waits, chosen so a misconfigured mount is a message and not a 500.

    Found the hard way: the first run mounted a directory owned by root into a container that runs
    unprivileged, and the first upload died with PermissionError halfway through the request. The document
    is transient and nothing needs to survive a restart, so falling back to the system temporary directory
    is correct rather than a compromise. It says so, because a silent fallback on a deployment that meant
    to configure otherwise is its own problem.
    """
    configured = os.environ.get("PARSER_UPLOAD_DIR")
    for candidate in ([Path(configured)] if configured else []) + [Path("/tmp/oi-parser")]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_bytes(b"")
            probe.unlink()
            if configured and candidate != Path(configured):
                print(f"PARSER_UPLOAD_DIR={configured} is not writable; using {candidate}")
            return candidate
        except OSError as exc:
            if configured and candidate == Path(configured):
                print(f"PARSER_UPLOAD_DIR={configured} is not usable ({exc}); falling back")
            continue
    raise RuntimeError("no writable directory for uploads")


UPLOAD_DIR = _upload_dir()

# Options this service accepts, and the only ones. Sending an unknown field is refused with the list,
# because docling-serve ignores unknown fields while answering success and that has cost this project
# three round trips: a nested options object, and two invented option names.
ACCEPTED_FIELDS = frozenset({
    "to_formats", "image_export_mode", "ocr", "table_structure", "force_ocr", "max_num_pages",
})
SUPPORTED_FORMATS = frozenset({"json"})
SUPPORTED_IMAGE_MODES = frozenset({"placeholder", "embedded", "referenced"})

# How many conversions may run at once. One, because the models are the memory and a second concurrent
# conversion is a second copy of them on a box sized for one.
MAX_CONCURRENT = int(os.environ.get("PARSER_MAX_CONCURRENT", "1"))

_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()
_semaphore = threading.Semaphore(MAX_CONCURRENT)
_converter_lock = threading.Lock()
_converters: dict[tuple, DocumentConverter] = {}


def _converter(ocr: bool, table_structure: bool, force_ocr: bool) -> DocumentConverter:
    """One converter per option combination, cached, because loading the models is the slow part.

    Held behind a lock because two requests arriving together would otherwise both build one and both load
    the weights, which on a 2 GB model set is the difference between a queue and an out-of-memory kill.
    """
    key = (ocr, table_structure, force_ocr)
    with _converter_lock:
        if key not in _converters:
            options = PdfPipelineOptions()
            # Assigned through the model's own field set rather than assumed. `force_ocr` is NOT a field on
            # the pipeline options in this release; forcing a full-page pass is
            # `ocr_options.force_full_page_ocr`. Setting a field that does not exist raised at request time
            # with a pydantic error naming the class, which is the good version of this failure, and the
            # reason the assignments below are checked against `model_fields` first is to keep it that good.
            fields = type(options).model_fields
            wanted = {"do_ocr": ocr, "do_table_structure": table_structure}
            for name, value in wanted.items():
                if name not in fields:
                    raise RuntimeError(
                        f"this docling build has no {name!r} on PdfPipelineOptions; it has "
                        f"{sorted(f for f in fields if 'ocr' in f or 'table' in f)}")
                setattr(options, name, value)
            if force_ocr:
                if not hasattr(options.ocr_options, "force_full_page_ocr"):
                    raise RuntimeError(
                        "this docling build cannot force a full-page OCR pass: no "
                        "force_full_page_ocr on the OCR options")
                options.ocr_options.force_full_page_ocr = True
            _converters[key] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        return _converters[key]


def _page_report(confidence: Any) -> dict | None:
    """The per-page confidence, flattened for the wire.

    NaN becomes null, because NaN is not valid JSON and a client that parses it as null and a client that
    fails are two different bugs. A page whose ocr_score is null is a page OCR did not touch, which is the
    fact the pipeline needs to label a figure as read from an image.
    """
    pages = getattr(confidence, "pages", None)
    if not pages:
        return None
    out: dict[str, dict] = {}
    for key, page in pages.items():
        dumped = page.model_dump()
        out[str(key)] = {
            name: (None if isinstance(value, float) and value != value else value)
            for name, value in dumped.items()
        }
    return out


def _document_report(confidence: Any) -> dict | None:
    if confidence is None:
        return None
    dumped = confidence.model_dump()
    # The document-level report repeats the page map inside itself when serialised; the page map is
    # returned beside it instead so a client reads it once.
    dumped.pop("pages", None)
    return {name: (None if isinstance(value, float) and value != value else value)
            for name, value in dumped.items()}


def _build_options(form: dict) -> tuple[dict, list[str]]:
    """Read the request's options, and refuse anything not understood.

    Refusing rather than ignoring is the whole point. `docling-serve` accepts an unknown field, ignores it,
    and answers success, which is how `do_ocr`, `do_table_structure` and `table_mode` all looked like they
    worked while doing nothing at all.
    """
    supplied = {k: v for k, v in form.items() if k not in ("files",)}
    unknown = sorted(set(supplied) - ACCEPTED_FIELDS)
    errors: list[str] = []
    if unknown:
        errors.append(
            f"unknown option(s) {unknown}. This service refuses them rather than ignoring them. "
            f"Accepted: {sorted(ACCEPTED_FIELDS)}")

    to_formats = supplied.get("to_formats", "json")
    if isinstance(to_formats, list):
        to_formats = to_formats[0] if to_formats else "json"
    if to_formats not in SUPPORTED_FORMATS:
        errors.append(f"to_formats={to_formats!r} is not supported; this service returns json only")

    image_mode = supplied.get("image_export_mode", "placeholder")
    if image_mode not in SUPPORTED_IMAGE_MODES:
        errors.append(
            f"image_export_mode={image_mode!r} is not supported; one of {sorted(SUPPORTED_IMAGE_MODES)}")

    def as_bool(name: str, default: bool) -> bool:
        raw = supplied.get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    options = {
        "ocr": as_bool("ocr", True),
        "table_structure": as_bool("table_structure", True),
        "force_ocr": as_bool("force_ocr", False),
        "image_export_mode": image_mode,
    }
    raw_pages = supplied.get("max_num_pages")
    if raw_pages is not None:
        try:
            options["max_num_pages"] = int(raw_pages)
        except (TypeError, ValueError):
            errors.append(f"max_num_pages={raw_pages!r} is not an integer")
    return options, errors


def _run(task_id: str, path: Path, filename: str, options: dict) -> None:
    """Convert one document and record the outcome against its task id."""
    with _semaphore:
        started = time.time()
        try:
            converter = _converter(options["ocr"], options["table_structure"], options["force_ocr"])
            max_pages = options.get("max_num_pages")
            result = converter.convert(
                path, **({"max_num_pages": max_pages} if max_pages else {}))
            document = result.document
            # The library's own export, with coordinates rounded, rather than `model_dump_json`. That method
            # cannot render an image reference at all, and it raises rather than dropping one: the first
            # attempt at this service died on `unexpected keyword argument 'images'`. `export_to_dict` is
            # what the library uses to produce the JSON this pipeline already consumes, so the output stays
            # the shape the worker expects.
            #
            # `exclude_none` is left at its default so an absent table cell does not become a null the
            # chunker has to defend against, and coordinates are rounded to one decimal because sub-pixel
            # precision in a citation payload is noise that costs bytes.
            json_content = document.export_to_dict(coord_precision=1)
            payload = {
                "document": {"json_content": json_content},
                "confidence": _document_report(getattr(result, "confidence", None)),
                "confidence_pages": _page_report(getattr(result, "confidence", None)),
                "status": "success",
                "errors": [str(e) for e in (getattr(result, "errors", None) or [])],
                "processing_time": time.time() - started,
                "filename": filename,
            }
            state = {"task_status": "success", "result": payload}
        except Exception as exc:  # noqa: BLE001 - a failure is a result, not a crash
            state = {
                "task_status": "failure",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-2000:],
            }
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        with _tasks_lock:
            _tasks[task_id].update(state)
            _tasks[task_id]["finished_at"] = time.time()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    return {"status": "ok"}


@app.post("/v1/convert/file/async")
async def convert_async(request: Request, files: UploadFile) -> JSONResponse:
    """Accept a document and return a task id, the same way docling-serve does.

    The form is read as a whole rather than declared field by field, and that is deliberate. Declaring them
    would make FastAPI accept the ones it knows and drop the rest, so an option this service does not
    understand would vanish before `_build_options` could refuse it. Silence about an unknown option is the
    bug that made `do_ocr` and `table_mode` look like they worked for three round trips.
    """
    form = await request.form()
    out = UPLOAD_DIR / f"{uuid.uuid4().hex}-{Path(files.filename or 'document').name}"
    out.write_bytes(await files.read())
    options, errors = _build_options({key: value for key, value in form.multi_items()
                                      if key != "files"})
    if errors:
        out.unlink(missing_ok=True)
        return JSONResponse({"detail": errors}, status_code=422)

    task_id = str(uuid.uuid4())
    with _tasks_lock:
        _tasks[task_id] = {"task_id": task_id, "task_status": "pending",
                           "started_at": time.time(), "filename": files.filename}
    threading.Thread(target=_run, args=(task_id, out, files.filename or "document", options),
                     daemon=True).start()
    return JSONResponse({"task_id": task_id, "task_status": "pending"})


@app.get("/v1/status/poll/{task_id}")
def poll(task_id: str, wait: int = 0) -> dict:
    """Report a task's state, optionally waiting a little for it to finish."""
    deadline = time.time() + max(0, min(wait, 30))
    while True:
        with _tasks_lock:
            task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task["task_status"] in ("success", "failure") or time.time() >= deadline:
            return {"task_id": task_id, "task_status": task["task_status"]}
        time.sleep(1)


@app.get("/v1/result/{task_id}")
def result(task_id: str) -> JSONResponse:
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["task_status"] == "failure":
        return JSONResponse({"failure": {"error": task.get("error"),
                                         "traceback": task.get("traceback")}}, status_code=200)
    if task["task_status"] != "success":
        raise HTTPException(status_code=404, detail="task not finished")
    return JSONResponse(task["result"])


@app.get("/v1/confidence/{task_id}")
def confidence(task_id: str) -> dict:
    """The per-page confidence on its own, for a caller that wants to look before it converts.

    Returned inside the result as well, so a worker needs one call rather than two. This endpoint exists so
    the fact can be inspected without parsing a 60-page document's text out of the same payload.
    """
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None or task["task_status"] != "success":
        raise HTTPException(status_code=404, detail="no successful task with that id")
    return {"confidence": task["result"]["confidence"],
            "confidence_pages": task["result"]["confidence_pages"]}
