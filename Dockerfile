FROM alpine:3.22 AS caddy

ARG TARGETARCH
ARG GO_VERSION=1.24.3
ARG XCADDY_VERSION=0.4.5
ARG CADDY_VERSION=latest
RUN apk update && apk add --no-cache \
    git \
    build-base \
    curl \
    tar && \
    GO_ARCH=$( [ "$TARGETARCH" = "arm64" ] && echo 'linux-arm64' || echo 'linux-amd64' ) && \
    GO_URL="https://go.dev/dl/go${GO_VERSION}.${GO_ARCH}.tar.gz" && \
    echo "Downloading go from ${GO_URL}" && \
    curl -fsSL "${GO_URL}" | tar xz -C / && \
    XCADDY_URL="https://github.com/caddyserver/xcaddy/releases/download/v${XCADDY_VERSION}/xcaddy_${XCADDY_VERSION}_linux_${TARGETARCH}.tar.gz" && \
    echo "Downloading xcaddy from ${XCADDY_URL}" && \
    curl -fsSL "${XCADDY_URL}" | tar xz -C / && \
    echo "Building caddy ${CADDY_VERSION}" && \
    PATH="${PATH}:/go/bin" ./xcaddy build "$CADDY_VERSION" && \
    mkdir -p /out && \
    mv caddy /out/caddy

FROM alpine:3.22 AS couchdb

ARG COUCHDB_VERSION=3.5.1
RUN apk update && apk add --no-cache \
    build-base \
    curl \
    erlang26 \
    erlang26-dev \
    erlang26-reltool \
    icu-dev \
    openssl-dev \
    git && \
    curl -fsSL "https://archive.apache.org/dist/couchdb/source/${COUCHDB_VERSION}/apache-couchdb-${COUCHDB_VERSION}.tar.gz" -o couchdb.tar.gz && \
    mkdir /couchdb-src && \
    tar xzf couchdb.tar.gz -C /couchdb-src --strip-components=1 && \
    cd /couchdb-src && \
    ./configure --disable-docs --disable-fauxton --js-engine=quickjs --disable-spidermonkey && \
    make release && \
    mv /couchdb-src/rel/couchdb /out

# engraph binary (optional — if cargo build fails, a stub is placed; setup_engraph handles first-run build)
FROM alpine:3.22 AS engraph
RUN apk add --no-cache rust cargo git
# Place a stub first so COPY --from always succeeds; overwrite with real binary if build succeeds
RUN mkdir -p /usr/local/bin && \
    printf '#!/bin/sh\necho "[engraph] not yet built — run setup_engraph"\n' > /usr/local/bin/engraph && \
    chmod +x /usr/local/bin/engraph
RUN cargo install --git https://github.com/devwhodevs/engraph --root /usr/local 2>&1 | tail -5 || \
    echo "[engraph] build failed — stub binary retained; index on first run"

FROM alpine:3.22

ARG TARGETARCH
ARG S6_OVERLAY_VERSION=3.2.0.2
ARG S6_OVERLAY_BASE_URL="https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}"
RUN apk update && apk add --no-cache \
    ca-certificates \
    tzdata \
    curl \
    tar \
    xz \
    erlang26 \
    icu-libs \
    openssl \
    python3 \
    py3-pip \
    git \
    deno \
    jq \
    py3-yaml \
    py3-jsonschema && \
    ARCH=$( [ "$TARGETARCH" = "arm64" ] && echo aarch64 || echo x86_64 ) && \
    curl -fsSL "${S6_OVERLAY_BASE_URL}/s6-overlay-${ARCH}.tar.xz" | tar xJ -C / && \
    curl -fsSL "${S6_OVERLAY_BASE_URL}/s6-overlay-noarch.tar.xz" | tar xJ -C / && \
    addgroup -g 5984 couchdb && \
    adduser -D -u 5984 -G couchdb -h /home/couchdb couchdb

COPY --from=caddy /out/caddy /opt/bin/caddy
COPY --from=couchdb /out /opt/bin/couchdb
COPY --from=engraph /usr/local/bin/engraph /usr/local/bin/engraph

# Python services
RUN pip install --break-system-packages "radicale>=3.2" "hermes-agent"
COPY everstone_tasks /opt/everstone_tasks
RUN pip install --break-system-packages /opt/everstone_tasks
COPY access_hook /opt/access_hook
RUN pip install --break-system-packages /opt/access_hook

# livesync-bridge (requires github.com access at build time)
# --recurse-submodules: bridge embeds `lib/` as a submodule (main.ts imports from it)
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
        https://github.com/vrtmrz/livesync-bridge /opt/livesync-bridge || \
    echo "[livesync-bridge] clone failed — /opt/livesync-bridge absent; install manually at runtime"

# Pre-cache deno deps (jsr.io + npm) so the bridge starts offline-tolerantly
RUN if [ -f /opt/livesync-bridge/main.ts ]; then \
        cd /opt/livesync-bridge && \
        deno install --node-modules-dir=auto --entrypoint main.ts 2>&1 | tail -20 || \
        deno cache --node-modules-dir=auto main.ts 2>&1 | tail -20 || \
        echo "[livesync-bridge] deno dep pre-cache failed — will retry at runtime"; \
    fi

COPY scripts /scripts
COPY services /services
COPY config /opt/defaults/config

ENV PATH="${PATH}:/command:/scripts:/opt/bin:/usr/local/bin"
ENTRYPOINT ["/scripts/entrypoint"]
EXPOSE 80
VOLUME ["/opt/config.yaml", "/opt/data"]
