# The key tool: one small image, no dependencies beyond the standard library.
#
# It has to run on the ai-net network, because the gateway publishes no port. That is the reason it
# is a container rather than a script on the host, not a preference for containers.
FROM python:3.12-slim

WORKDIR /app
COPY services/gateway/keys.py /app/keys.py

# No entrypoint arguments: the tool reads the rendered manifest and the rendered env, both mounted
# from the deployment root, so nothing about the deployment is baked into the image.
ENTRYPOINT ["python", "/app/keys.py"]
