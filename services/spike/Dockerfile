# A throwaway image for the custom_auth spike, built on the same pinned digest as the gateway so the behaviour
# under test is the version we run. Not a service and it does not ship.
#
# No USER line: the pinned image runs as root already, which the gateway container confirms with `id`. The
# first version of this file copied the pattern from our own images and named a user that does not exist here.
FROM ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500

COPY custom_auth.py /app/custom_auth.py
