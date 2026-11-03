# The workspace service image.
#
# It carries the Docker CLI because it launches task containers on the workspace host, and it holds the Docker
# socket at run time. It does not carry the task image's tools; those are in the task image built from
# services/workspace/Dockerfile.
FROM docker:27-cli AS cli
FROM python:3.12-slim

COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
# The artifact store imports boto3 lazily. The container needs it for S3 artifact upload;
# the host-process run in October got it from the system interpreter instead.
RUN pip install --no-cache-dir boto3

COPY services/workspace/service.py services/workspace/workspace.py services/workspace/artifact_store.py /app/
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/service.py"]
