# Postgres, with the one binary the upstream image does not rebuild.
#
# CVE-2025-68121 is in the Go standard library compiled into gosu, which the postgres image uses only to drop
# from root to the postgres user. The upstream postgres:16-alpine image, and Alpine's prebuilt gosu package,
# still carry a vulnerable Go runtime. This builds gosu from source with a patched Go toolchain and replaces
# the binary at the path the entrypoint already calls.
#
# Nothing else changes. The database version, entrypoint, configuration and data directory are upstream's.
FROM golang:1.25-alpine AS gosu-build

RUN apk add --no-cache git \
    && git clone --depth 1 https://github.com/tianon/gosu.git /src/gosu \
    && cd /src/gosu \
    && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /gosu .

FROM postgres:16-alpine

COPY --from=gosu-build /gosu /usr/local/bin/gosu
RUN gosu --version >/dev/null
