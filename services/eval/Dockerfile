# The evaluation harness, as a tool rather than a service.
#
# Its own image because it needs two things the worker does not: a YAML parser for the question set, and the
# retrieval half of the codebase, which talks to Postgres through psycopg2 and to the embedding service over
# HTTP. Putting a YAML parser into the ingestion image to serve a tool would be the tool deciding what the
# worker carries.
FROM python:3.12-slim

RUN pip install --no-cache-dir "PyYAML==6.0.2" "psycopg2-binary==2.9.13"

WORKDIR /app
# The harness itself, and nothing from the repository. The retriever and the ingestion modules it imports at run
# time come from the mounted checkout, so the image does not carry a second copy of them that could be stale.
COPY harness.py ./

RUN useradd --create-home --uid 10003 evaluator

USER evaluator
ENTRYPOINT ["python", "-c", "import sys; sys.path.insert(0, '/app'); import harness; sys.exit(harness.main())"]
