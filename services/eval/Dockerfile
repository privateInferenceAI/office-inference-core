# The evaluation harness, as a tool rather than a service.
#
# Its own image because it needs two things the worker does not: a YAML parser for the question set, and the
# retrieval half of the codebase, which talks to Postgres through psycopg2 and to the embedding service over
# HTTP. Putting a YAML parser into the ingestion image to serve a tool would be the tool deciding what the
# worker carries.
FROM python:3.12-slim

# PyYAML for the question set, psycopg2 for the retrieval half, and botocore because the reranker signs its
# requests through it rather than computing a SigV4 signature itself. Found by running the reranker acceptance:
# the retriever now imports the reranker, so whatever image runs a retrieval needs the AWS SDK in it, and this
# one did not.
RUN pip install --no-cache-dir "PyYAML==6.0.2" "psycopg2-binary==2.9.13" "botocore==1.35.90"

WORKDIR /app
# The harness itself, and nothing from the repository. The retriever and the ingestion modules it imports at run
# time come from the mounted checkout, so the image does not carry a second copy of them that could be stale.
COPY harness.py ./

RUN useradd --create-home --uid 10003 evaluator

USER evaluator
ENTRYPOINT ["python", "-c", "import sys; sys.path.insert(0, '/app'); import harness; sys.exit(harness.main())"]
