FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"

# The statement verifier as well as the tool, because a record written by the API arrives with a signed
# statement naming its actor and this is what checks it. Shared with the API's copy rather than reimplemented,
# so the writer and the reader cannot disagree about what a valid statement is.
COPY services/audit/audit.py /app/audit.py
COPY services/gateway/assertion.py /app/assertion.py

# migrations and exports are mounted from the deployment root at run time
ENV MIGRATIONS_DIR=/app/migrations \
    EXPORTS_DIR=/app/exports

ENTRYPOINT ["python", "/app/audit.py"]
