# The session tool: establish a session, inspect one, and rehearse the refusal.
#
# Its own image because it owns a table and a boundary nothing else does, and because the assistant API will
# import the same module rather than reimplement the check. It carries the access model from
# services/retrieval because the retriever and this tool have to agree about what a root is, and one
# definition is the only way that stays true.
#
# The build context is the repository root for that reason: the Dockerfile reaches outside its own directory
# the same way the keys service does.
FROM python:3.12-slim

WORKDIR /app
# **Two Postgres drivers, and the second one is for the probes rather than for the tool.** The audit tool owns
# the chain and uses psycopg 3; the retrieval half uses psycopg2. `access_probe` reaches into both — it reads the
# ACL contract through the retriever and reads refusal rows straight from the table, because a refusal that
# reported its own success would prove nothing. Without psycopg2 that probe dies on an import, and the runbook
# documents it as running under this image's entrypoint.
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*" "psycopg2-binary==2.9.*"

COPY services/session/session.py /app/session.py
COPY services/session/cli.py /app/cli.py
COPY services/retrieval/access.py /app/access.py

# The rendered registry, the migrations, and the audit tool are mounted from the deployment root at run time,
# so a render or a migration takes effect without a rebuild. AUDIT_TOOL is where the refusal is recorded:
# the session tool does not write the chain, because a second writer is a second definition of the hash.
ENV OI_ROOT=/opt/oi-core \
    AUDIT_TOOL=/app/audit.py \
    MIGRATIONS_DIR=/app/migrations

ENTRYPOINT ["python", "/app/cli.py"]
