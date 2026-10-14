# The key tool and the gateway's identity hook.
#
# The hook lives in this image rather than being mounted, because it is a security control: a file that can be
# changed under a running container without a rebuild is a control nobody reviewed. The context is the
# repository root so the image can carry services/gateway/ in one piece.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"

# keys.py is the tool a deployment runs; custom_auth.py is what the gateway imports when the assistant API
# presents an assertion; assertion.py is the verifier they share, which is the point: one implementation of
# the signature, so the minting side and the checking side cannot disagree.
COPY services/gateway/keys.py services/gateway/custom_auth.py services/gateway/assertion.py /app/

# The rendered directory is mounted at run time, holding the manifest, the env file and the signing key.
ENV RENDERED_DIR=/app/rendered

ENTRYPOINT ["python", "/app/keys.py"]