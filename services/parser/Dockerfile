# The parser service: docling-serve's container, our entrypoint.
#
# Why reuse that image rather than build one. It already carries the layout, table and OCR weights, the
# pinned versions of docling and its satellites, and the CPU-tuned thread settings. Building a second image
# would mean carrying a second copy of 2 GB of weights on the box and keeping two dependency sets in step,
# to change one Python file.
#
# What changes is the entrypoint: docling-serve's HTTP layer is replaced by ours, which returns the whole
# confidence report instead of the document-level rollup.
# ghcr.io/docling-project/docling-serve-cpu:v1.36.0, released 2026-10-01, digest-pinned the same way
# the compose file pinned it. A tag would be a different image next month, and this container reads
# client documents in their original form.
FROM ghcr.io/docling-project/docling-serve-cpu@sha256:225c8586e20d5d0fc6811a9e0e044fa602bcc4393f00389009bad42d6787b58f

# The service is one file and it imports only what the image already installs: fastapi, uvicorn, docling.
COPY parser_service.py /app/parser_service.py

# **LibreOffice, because Docling needs it for RTF and this was the only thing missing for that format.**
#
# Docling's format table lists `RTF | Rich Text Format; requires LibreOffice`, and it registers RTF properly:
# `InputFormat.RTF` maps to `WordFormatOption()`, whose backend shells out to convert the file to DOCX. Its
# `get_libreoffice_cmd()` returned `None` in this image because the binary was not installed, so every RTF
# failed with `LibreOffice is required to convert a .rtf file to .docx` -- which the ingestion worker sees as
# a generic `RuntimeError`, classifies as a PERMANENT failure, and dead-letters after two cycles.
#
# The symptom was therefore a supported format silently never being indexed. Nothing in the pipeline said a
# binary was missing; the manifest recorded a failure count, and the reason sat unread in `last_error`.
#
# `libreoffice-writer` rather than the full suite: 332 MB installed against several times that, and it is the
# component that opens a Word or RTF document. `--setopt=install_weak_deps=False` keeps out the fonts, Java
# runtimes and mail-merge extras that arrive as recommendations and none of which a converter needs.
#
# No application code changes. Docling finds the binary itself with `shutil.which` and already isolates its
# own LibreOffice profile per process and applies a timeout, so the library handles concurrency rather than us.
#
# **The image runs as uid 1001 with `HOME=/opt/app-root/src`, which is writable.** That matters: LibreOffice
# creates a user profile on first use and fails without somewhere to put it. It was checked, not assumed.
RUN set -eux; \
    dnf install -y --setopt=install_weak_deps=False libreoffice-writer; \
    dnf clean all; \
    rm -rf /var/cache/dnf; \
    command -v soffice >/dev/null; \
    python -c "from docling.backend.docx.drawingml.utils import get_libreoffice_cmd; \
assert get_libreoffice_cmd(), 'Docling cannot find LibreOffice, so .rtf will dead-letter again'"

ENV PYTHONUNBUFFERED=1 \
    PARSER_UPLOAD_DIR=/tmp/oi-parser

# Same port as the service it replaces, so nothing else in the deployment moves. Running as the image's
# own user rather than root, which is what it already does.
EXPOSE 5001

HEALTHCHECK --interval=15s --timeout=10s --retries=5 --start-period=300s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health', timeout=4)"

CMD ["uvicorn", "parser_service:app", "--host", "0.0.0.0", "--port", "5001", \
     "--workers", "1", "--timeout-keep-alive", "75", "--app-dir", "/app"]
