# The MCP gateway image.
#
# It reuses the API image because that image already carries the Postgres driver and the audit tool the
# gateway needs to read a session and record a refusal. The gateway code is the only application file it adds;
# the credential and the registry are mounted at run time so a rotation or a registry change needs no rebuild.
FROM oi-core/api:local

COPY services/mcp_gateway/gateway.py /app/gateway.py
WORKDIR /app
ENTRYPOINT ["python", "/app/gateway.py"]
