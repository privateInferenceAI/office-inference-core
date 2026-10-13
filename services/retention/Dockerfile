# The retention service: the commands that delete a client's document and, at the end, everything.
#
# Its own image rather than a command inside the audit service, because it reaches stores the audit service does
# not know about: a command that clears the vector index should not live inside the thing that records what
# happened, or the recorder becomes a component that can delete the record.
FROM python:3.12-slim

RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"

WORKDIR /app
COPY retention.py cli.py ./

RUN useradd --create-home --uid 10002 retention

USER retention
ENTRYPOINT ["python", "/app/cli.py"]
