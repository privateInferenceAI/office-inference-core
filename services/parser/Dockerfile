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

# **Two critical Python packages in the pinned base.** certifi 2026.7.22 carries the compromised emSign
# intermediate CA (GHSA-6q48-6c7w-9gw2), and Pillow 12.3.0 has four critical image-parsing CVEs fixed in
# 13.0.0. Docling pulls both. Upgrading them here keeps the base image, weights and entrypoint unchanged;
# the parser probe has to prove Pillow 13 still reads the document formats the service advertises.
USER root
RUN /opt/app-root/bin/python -m pip install --no-cache-dir --upgrade \
    "certifi>=2026.10.19" "pillow>=13.0.0" \
    # **pip is removed after the one install.** Its vendored certifi is the old package the scanner
    # reports; the runtime does not need pip. Only the upgraded application packages remain.
    && rm -rf /opt/app-root/lib64/python3.12/site-packages/pip \
              /opt/app-root/lib64/python3.12/site-packages/pip-*.dist-info \
              /opt/app-root/bin/pip*

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
# **The full converter set, and the earlier Writer-only decision was wrong.** It was made to save image size:
# `libreoffice-writer` is 332 MB installed against several times that for the suite. The saving was real and
# the consequence was not acceptable. Writer reads Word and RTF; it cannot open a spreadsheet or a slide deck,
# so every `.xls` and `.ppt` a client had was refused, and refused **in silence** -- the suffix was absent from
# the worker's accepted list, so the file was never submitted to the parser and nothing anywhere recorded that
# it had been left out. A client's bookkeeping since 2009 was not in the index and no one could say why.
#
# Docling's own format table lists `XLS` and `PPT`, so the parser advertised support for both while the image
# could not read either. **Calc and Impress are the components that close that**, and the build asserts the
# three registry files exist rather than trusting the package names, because a base-image change that dropped
# one would otherwise be invisible until a client's file failed.
#
# `--setopt=install_weak_deps=False` keeps out the fonts, Java runtimes and mail-merge extras that arrive as
# recommendations and none of which a converter needs.
#
# No application code changes. Docling finds the binary itself with `shutil.which` and already isolates its
# own LibreOffice profile per process and applies a timeout, so the library handles concurrency rather than us.
#
# **The image runs as uid 1001 with `HOME=/opt/app-root/src`, which is writable.** That matters: LibreOffice
# creates a user profile on first use and fails without somewhere to put it. It was checked, not assumed.
# **`USER root` is required, and the base image's default user is why.** It sets `USER 1001`, so a plain
# `RUN dnf install` fails with "This command has to be run with superuser privileges" -- the build runs as
# the unprivileged account, which is a good default for a runtime and no help for a package manager. The
# user is switched back immediately, so the image's runtime identity is unchanged.
USER root
RUN set -eux; \
    dnf install -y --setopt=install_weak_deps=False libreoffice-writer libreoffice-calc libreoffice-impress; \
    dnf clean all; \
    rm -rf /var/cache/dnf; \
    command -v soffice >/dev/null; \
    python -c "from docling.backend.docx.drawingml.utils import get_libreoffice_cmd; \
assert get_libreoffice_cmd(), 'Docling cannot find LibreOffice, so .rtf will dead-letter again'; \
import subprocess; \
missing = [f for f in ('calc.xcd', 'impress.xcd', 'writer.xcd') \
           if not __import__('pathlib').Path('/usr/lib64/libreoffice/share/registry/' + f).exists()]; \
assert not missing, f'LibreOffice is missing the components that read legacy Office files: {missing}'"
USER 1001

ENV PYTHONUNBUFFERED=1 \
    PARSER_UPLOAD_DIR=/tmp/oi-parser

# Same port as the service it replaces, so nothing else in the deployment moves. Running as the image's
# own user rather than root, which is what it already does.
EXPOSE 5001

HEALTHCHECK --interval=15s --timeout=10s --retries=5 --start-period=300s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health', timeout=4)"

CMD ["uvicorn", "parser_service:app", "--host", "0.0.0.0", "--port", "5001", \
     "--workers", "1", "--timeout-keep-alive", "75", "--app-dir", "/app"]
