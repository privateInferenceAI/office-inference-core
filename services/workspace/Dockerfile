# The task image a workspace container runs.
#
# It is deliberately small and pinned by digest in Compose, like every other image. A task gets the languages and
# utilities it needs for its work, not a general-purpose developer environment: the image is the allowlist.
# No credential is baked in and no secret is read from the environment.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends bash coreutils jq ca-certificates time \
 && rm -rf /var/lib/apt/lists/*

# The task directory is mounted here by the sandbox manager. The root filesystem is read-only at run time, so
# the only writable place the task gets is /workspace and the tmpfs at /tmp.
WORKDIR /workspace
