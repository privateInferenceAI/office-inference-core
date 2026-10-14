# The assistant API.
#
# A service rather than a tool, because it listens. Its context is the repository root because it imports
# three other services: retrieval (the search and the citation checks), ingestion (the endpoints Qdrant and
# TEI are reached through), session (who is asking) and gateway (how the model call is authenticated).
#
# That is the same choice the retrieval module already made: one definition of each thing, shared, rather than
# a copy per image that drifts.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "fastapi==0.143.0" "uvicorn[standard]==0.38.0" "psycopg[binary]==3.2.*" \
    "psycopg2-binary==2.9.13" "botocore==1.35.90"

WORKDIR /app
COPY services /app/services

ENV PYTHONUNBUFFERED=1 \
    OI_GATEWAY_URL=http://litellm:4000 \
    ACCESS_REGISTRY=/app/rendered/access.json \
    OI_ASSERTION_KEY=/app/rendered/assertion.key \
    AUDIT_TOOL=/app/audit.py

CMD ["uvicorn", "services.api.app:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1"]