FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir \
      "psycopg[binary]==3.2.*" \
      "httpx==0.28.*" \
      "qdrant-client==1.12.*" \
      "pypdf==5.*" \
      "python-docx==1.1.*"

COPY services/ingestion/ /app/

ENV MIGRATIONS_DIR=/app/migrations
ENTRYPOINT ["python"]
