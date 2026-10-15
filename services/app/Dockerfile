# The client app.
#
# **Two stages, because the build needs Node and the runtime needs none of it.** The bundle is three static
# files: an HTML page, a stylesheet, and one JavaScript file. Shipping Node into a deployment to serve them
# would add a runtime and a dependency tree for no benefit, and every dependency on a client's box is something
# that has to be patched.
#
# **The runtime is Python, and that is deliberate.** What serves the app is the same small proxy the workbench
# uses: static files, and `/api/*` forwarded to the assistant API. The app calls a relative `/api`, so the
# question is only who answers it. One implementation of that answer, shared with the tool the runbook uses to
# reach a deployment by hand.
#
# **The build context is the client app's checkout, not this repository.** The Dockerfile sits here because
# this is where the deployment's services live, and it is invoked with `context:` pointing at `OI_UI_ROOT`
# through `dockerfile:` pointing back at this file. That keeps one repository the deployment and one the app,
# which is the arrangement the API boundary exists to allow.

FROM node:22-alpine AS bundle
WORKDIR /app
# **The lockfile first, and `npm ci` rather than `npm install`.** The first layer is cached until the
# dependencies actually change, and `ci` refuses when the lockfile and the manifest disagree, which is the
# difference between reproducing a build and hoping for one.
COPY app/package.json app/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY app/ ./
# The contract is copied rather than imported, so this fails loudly if the app was built against a different
# API than the one the core publishes. A missing file here is a build that would have produced a broken client.
COPY docs/openapi.json /contract/openapi.json
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv
COPY --from=bundle /app/dist ./public
COPY scripts/workbench_server.py ./workbench_server.py

# **Two variables, and neither is a secret.** The app holds no model key and no database credential, because
# everything it knows arrives through the API. This is the constraint the whole architecture rests on, so it is
# visible here rather than asserted in a comment somewhere.
ENV APP_PORT=8080 \
    OI_API_URL=http://api:8088/api

EXPOSE 8080
# Bound to 0.0.0.0 because a container's own loopback is not the network the deployment reaches it on, and
# nothing is published at the compose level: a deployment puts whatever it likes in front.
CMD ["sh", "-c", "exec python workbench_server.py --port ${APP_PORT} --bind 0.0.0.0 --root ./public --api ${OI_API_URL}"]
