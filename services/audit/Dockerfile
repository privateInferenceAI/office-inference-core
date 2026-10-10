FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"

COPY services/audit/audit.py /app/audit.py

# migrations and exports are mounted from the deployment root at run time
ENV MIGRATIONS_DIR=/app/migrations \
    EXPORTS_DIR=/app/exports

ENTRYPOINT ["python", "/app/audit.py"]
