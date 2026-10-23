# Workspace service, first slice

**Date:** 2026-10-23. **Status:** the internal HTTP service over the sandbox is built and live-tested. This is not
the WS gate.

## What was built

- `services/workspace/service.py`: a token-guarded HTTP service with `/health`, `/run`, `/shell`, `/write`,
  `/read`, `/list` and `/cleanup`. It refuses to start without `WORKSPACE_TOKEN`. It caps body size and
  output, checks task paths, and maps sandbox timeout, quota and errors to typed responses.
- `services/workspace/service.Dockerfile`: the service image with the Docker CLI.
- `tests/test_workspace_service.py`: 8 tests over auth, files, paths, run, cleanup, body cap and unknown routes.
  Together with the 8 sandbox tests, 16 pass.

## Live proof on the box

The service ran on the box at `127.0.0.1:8090` with a test token against the real Docker sandbox.

- `GET /health` returned 200 without a token.
- `POST /list` without a token returned 401.
- `POST /write` wrote `a.txt` (13 bytes, sha256 `0deffcd5...`).
- `POST /read` returned the same bytes.
- `POST /run` ran `python3 -c print(open('/workspace/a.txt').read())` and returned `service hello`.
- `POST /cleanup` removed the task.
- `GET /health` still returned 200.

## What this does not prove

- The service running as a container next to the Docker socket. The live run was the service process on the box
  host.
- The API and runtime calling the service as a tool.
- The egress proxy, S3 artifacts, workspace UI and first connector.

## Next

Run the service as a container with the socket on the workspace host, then add the runtime's workspace tools and
a definition that uses them. Then egress, artifacts, UI and connector.
