# The ingestion worker's image.
#
# Slim rather than the parser image. This worker makes HTTP calls and writes database rows; it needs no
# document parsing libraries, because parsing is the parser service's job. Using the 2 GB Docling image
# here would put a second copy of every model weight on the box to make three HTTP requests.
#
# python:3.12-slim has no compiler, so the Postgres driver has to come as a wheel. psycopg2-binary
# publishes one for cp312 on manylinux x86_64, verified against PyPI on 2026-10-12, which is why the
# dependency is pinned to a version rather than left floating: a version without that wheel turns the
# build into a compiler problem on a box that has none.
FROM python:3.12-slim

# Pinned for the same reason every other image in this file is: an unpinned base is a different image
# next month, and this worker sits on the path that reads client documents.
# python:3.12-slim as of 2026-10-12
LABEL org.opencontainers.image.title="oi-core ingestion worker"
LABEL org.opencontainers.image.description="Documents become chunks in the vector store, once."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir "psycopg2-binary==2.9.13"

WORKDIR /app
# Only what this worker runs. chunker.py is here because it is the module that decides what a citation
# points at, and ingest.py is the policy around it.
# identifiers.py is the normalisation shared with the retriever, which imports it from this directory. One
# implementation, so a reference normalises the same way on the way in and on the way out of the search.
COPY chunker.py ingest.py identifiers.py contract.py ./

# The state directory holds the last cycle's result, which the healthcheck reads. Created here so the
# container can write it as a non-root user.
RUN useradd --create-home --uid 10001 worker && mkdir -p /state && chown worker /state
USER worker

# No port, no server. A cycle by default, which is what this image is for; the scheduled form is passed
# as a command by whatever runs it.
ENTRYPOINT ["python3", "/app/ingest.py"]
CMD ["--documents", "/documents", "--json"]
