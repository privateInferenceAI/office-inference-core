# The intake connector: read one mailbox the client owns, deposit what is in it, record where it came from.
#
# Its own image because it is the only component in the system that holds a credential into somebody else's
# mail system, and because that credential must not be in the image, in the environment, or reachable from the
# application container. A separate image is what makes "the application container cannot read it" a fact
# about the deployment rather than a promise about the code.
#
# The build context is the repository root, because the Dockerfile borrows the ingestion worker's format list
# rather than keeping a second copy of it.
FROM python:3.12-slim

WORKDIR /app

# PyYAML for the client's configuration. psycopg for the record's mirror and for `verify-manifest`, which
# joins this row to `ingest_manifest`. Both are pinned the way the rest of the stack pins things.
RUN pip install --no-cache-dir "PyYAML==6.0.*" "psycopg[binary]==3.2.*"

COPY services/intake/intake.py /app/intake.py
COPY services/intake/config.py /app/config.py
COPY services/intake/net.py /app/net.py
COPY services/intake/secretstore.py /app/secretstore.py
COPY services/intake/graph.py /app/graph.py
COPY services/intake/sender.py /app/sender.py
COPY services/intake/content.py /app/content.py
COPY services/intake/provenance.py /app/provenance.py
COPY services/intake/folder.py /app/folder.py
COPY services/intake/monitor.py /app/monitor.py
COPY services/intake/reach.py /app/reach.py
COPY services/intake/schema/ /app/schema/

# The ingestion worker's format list, imported rather than copied into this service. A second copy agrees on
# the day it is written and drifts on the day somebody adds a format, and the drift shows up as an invoice
# reported as ingested while the worker walks past it.
COPY services/ingestion/formats.py /app/formats.py
COPY services/ingestion/formats.json /app/formats.json

# **What is deliberately not here.** No credential, no `.env`, no client configuration, and no `COPY . .`
# that would sweep one in. `SEC-CON-001` asks for an image that holds no key, and the way to satisfy it is to
# name every file rather than to remember not to copy one.
#
# The path to the client's secret store arrives at run time, and the value never appears in this file, in an
# environment variable, or in `docker inspect` output.
ENV OI_INTAKE_CONFIG=/etc/oi-intake/intake.yaml

ENTRYPOINT ["python", "/app/intake.py"]
