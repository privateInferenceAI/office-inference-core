# The model gateway: LiteLLM at a pinned digest, plus this deployment's identity hook.
#
# A layer rather than a bare image reference, because the hook is a security control that has to be reviewed
# and rebuilt rather than mounted. It changes the gateway from something that takes a long-lived key to
# something that takes a short-lived signed statement, which is the whole of C3's credential decision.
#
# The base is pinned by digest and the version is recorded in the compose file beside it. The build context is
# the repository root so the image carries services/gateway/ in one piece.
FROM ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500

# The image installs LiteLLM in a virtualenv and runs from it, so the modules go beside the app rather than
# into a site-packages nobody imports from.
COPY services/gateway/custom_auth.py services/gateway/assertion.py /app/
