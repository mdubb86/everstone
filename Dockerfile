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

FROM debian:trixie-slim AS couchdb

ARG COUCHDB_VERSION=3.5.1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    erlang-dev \
    erlang-nox \
    erlang-reltool \
    libicu-dev \
    libssl-dev \
    pkg-config \
    git && \
    curl -fsSL "https://archive.apache.org/dist/couchdb/source/${COUCHDB_VERSION}/apache-couchdb-${COUCHDB_VERSION}.tar.gz" -o couchdb.tar.gz && \
    mkdir /couchdb-src && \
    tar xzf couchdb.tar.gz -C /couchdb-src --strip-components=1 && \
    cd /couchdb-src && \
    ./configure --disable-docs --disable-fauxton --js-engine=quickjs --disable-spidermonkey && \
    make release && \
    mv /couchdb-src/rel/couchdb /out && \
    rm -rf /var/lib/apt/lists/*

# engraph binary (optional — if cargo build fails, a stub is placed; setup_engraph handles first-run build)
FROM alpine:3.22 AS engraph
RUN apk add --no-cache rust cargo git
# Place a stub first so COPY --from always succeeds; overwrite with real binary if build succeeds
RUN mkdir -p /usr/local/bin && \
    printf '#!/bin/sh\necho "[engraph] not yet built — run setup_engraph"\n' > /usr/local/bin/engraph && \
    chmod +x /usr/local/bin/engraph
RUN cargo install --git https://github.com/devwhodevs/engraph --root /usr/local 2>&1 | tail -5 || \
    echo "[engraph] build failed — stub binary retained; index on first run"

# ── Hermes agent: canonical checkout + venv (replicates install.sh on Alpine) ──
# install.sh itself is unusable here (it downloads uv from astral.sh, which our
# build network blocks, and assumes apt/glibc). We reproduce its canonical layout
# explicitly: a git checkout + a uv venv with `.[all]`, plus our es CLI, the
# access_hook plugin, and the telegram adapter installed INTO that venv.
FROM debian:trixie-slim AS hermes-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-dev git \
        build-essential libffi-dev libssl-dev cargo curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages uv
# Canonical layout at a FIXED path — the final stage COPYs to the identical path
# so the venv's absolute paths + the editable install resolve.
RUN git clone --depth 1 --branch main \
        https://github.com/NousResearch/hermes-agent /usr/local/lib/hermes-agent
WORKDIR /usr/local/lib/hermes-agent
RUN uv venv && uv pip install -e '.[all]'
# Telegram adapter (NOT in .[all]) + es CLI + access_hook plugin go into the venv
# so Hermes runs one interpreter that can load the plugin (hermes_plugins entry
# point) and resolve `es`.
RUN uv pip install --python /usr/local/lib/hermes-agent/.venv/bin/python "python-telegram-bot>=21"
COPY es /opt/es
RUN uv pip install --python /usr/local/lib/hermes-agent/.venv/bin/python /opt/es
COPY access_hook /opt/access_hook
RUN uv pip install --python /usr/local/lib/hermes-agent/.venv/bin/python /opt/access_hook
RUN rm -rf /usr/local/lib/hermes-agent/.git

FROM debian:trixie-slim

ARG TARGETARCH
ARG S6_OVERLAY_VERSION=3.2.0.2
ARG S6_OVERLAY_BASE_URL="https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}"
# System deps for the s6-supervised services + the Hermes runtime, PLUS the
# Firefox/Camoufox shared libs (baked in here so sub-project B is just
# `pip install camoufox` + enable the browser toolset). Debian trixie uses the
# `t64` (64-bit time) names for several libs (gtk/asound/atk/cups).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    curl \
    tar \
    xz-utils \
    unzip \
    erlang-nox \
    libicu76 \
    openssl \
    python3 \
    python3-pip \
    python3-venv \
    git \
    jq \
    python3-yaml \
    python3-jsonschema \
    bash \
    ripgrep \
    findutils \
    coreutils \
    nodejs \
    npm \
    ffmpeg \
    libstdc++6 \
    libffi8 \
    libgtk-3-0t64 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libxkbcommon0 libpango-1.0-0 libcairo2 libasound2t64 libdbus-glib-1-2 \
    libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libnss3 libnspr4 libxtst6 \
    libxshmfence1 fonts-liberation && \
    ARCH=$( [ "$TARGETARCH" = "arm64" ] && echo aarch64 || echo x86_64 ) && \
    curl -fsSL "${S6_OVERLAY_BASE_URL}/s6-overlay-${ARCH}.tar.xz" | tar xJ -C / && \
    curl -fsSL "${S6_OVERLAY_BASE_URL}/s6-overlay-noarch.tar.xz" | tar xJ -C / && \
    curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip && \
    groupadd -g 5984 couchdb && \
    useradd -m -u 5984 -g 5984 -d /home/couchdb couchdb && \
    rm -rf /var/lib/apt/lists/*

COPY --from=caddy /out/caddy /opt/bin/caddy
COPY --from=couchdb /out /opt/bin/couchdb
COPY --from=engraph /usr/local/bin/engraph /usr/local/bin/engraph

# Hermes agent: canonical checkout+venv from the hermes-build stage. The final
# image carries NO compilers — only the runtime shared libs the musl wheels link
# against. The venv holds Hermes + es + access_hook + the telegram adapter (one
# interpreter). Hermes's own non-pip deps (Node, ffmpeg, ripgrep) are satisfied
# by the apt packages above — detected via shutil.which on PATH.
COPY --from=hermes-build /usr/local/lib/hermes-agent /usr/local/lib/hermes-agent
RUN ln -sf /usr/local/lib/hermes-agent/.venv/bin/hermes /usr/local/bin/hermes && \
    ln -sf /usr/local/lib/hermes-agent/.venv/bin/es-mcp /usr/local/bin/es-mcp

# radicale is EverStone's CalDAV server (run by s6, not imported by Hermes) —
# keep it decoupled in system python (pure-python, no build deps).
RUN pip install --break-system-packages "radicale>=3.2"

# livesync-bridge (requires github.com access at build time)
# --recurse-submodules: bridge embeds `lib/` as a submodule (main.ts imports from it)
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
        https://github.com/vrtmrz/livesync-bridge /opt/livesync-bridge || \
    echo "[livesync-bridge] clone failed — /opt/livesync-bridge absent; install manually at runtime"

# Pre-cache deno deps (jsr.io + npm) so the bridge starts offline-tolerantly
RUN echo "deno-precache v2" && \
    if [ -f /opt/livesync-bridge/main.ts ]; then \
        cd /opt/livesync-bridge && \
        ( deno install --node-modules-dir=auto --entrypoint main.ts || \
          deno cache --node-modules-dir=auto main.ts || \
          echo "[livesync-bridge] deno dep pre-cache failed — will retry at runtime" ); \
    fi

# hermes-webui: browser UI for the agent (Python stdlib, no build step). Runs
# under the agent venv python so it imports Hermes in-process. Tracks master.
RUN git clone --depth 1 --branch master \
        https://github.com/nesquena/hermes-webui /opt/hermes-webui || \
    echo "[hermes-webui] clone failed — /opt/hermes-webui absent; web UI unavailable"

# camofox-browser: Camoufox (stealth Firefox) wrapped in a Node REST server.
# Hermes's browser_* tools are an HTTP client to it (CAMOFOX_URL=localhost:9377).
# `npm install` runs its postinstall (scripts/postinstall.js) which fetches the
# Camoufox binary (~300MB) from GitHub releases → baked in for offline-tolerant
# starts. Node + the Firefox system libs are present from the Debian base.
RUN git clone --depth 1 \
        https://github.com/jo-inc/camofox-browser /opt/camofox-browser || \
    echo "[camofox-browser] clone failed — browser unavailable"
RUN if [ -f /opt/camofox-browser/package.json ]; then \
        cd /opt/camofox-browser && npm install 2>&1 | tail -5 || \
        echo "[camofox-browser] npm install failed — browser unavailable at runtime"; \
    fi

COPY scripts /scripts
COPY services /services
COPY config /opt/defaults/config

# Operator admin CLI: `esadmin` -> Typer app at /scripts/everstone_cli.py.
# Tab-completion: we install the script deterministically rather than
# calling Typer's --install-completion, because shellingham can't detect
# the parent shell during a BuildKit build (parent is /bin/sh, not bash)
# and fails with "Shell None is not supported." The static completion
# script is identical to what Typer would emit.
RUN printf '#!/bin/sh\nexec /usr/local/lib/hermes-agent/.venv/bin/python /scripts/everstone_cli.py "$@"\n' \
        > /usr/local/bin/esadmin && \
    chmod +x /usr/local/bin/esadmin && \
    chmod +x /scripts/everstone_cli.py && \
    mkdir -p /root/.bash_completions && \
    cp /scripts/everstone_completion.sh /root/.bash_completions/esadmin.sh && \
    echo "source /root/.bash_completions/esadmin.sh" > /root/.bashrc

ENV PATH="${PATH}:/command:/scripts:/opt/bin:/usr/local/bin"
# HERMES_HOME at container env level so `docker exec everstone hermes ...`
# (and `docker exec everstone everstone <cmd>` which exec's into it) find
# the data-volume profile dir. Services already export this in their s6
# run files for clarity but the container-level ENV is what makes ad-hoc
# operator commands work without -e flags.
ENV HERMES_HOME=/opt/data/hermes
# Point Hermes's browser_* tools at the in-container camofox-browser server
# (localhost:9377). Setting CAMOFOX_URL is what makes Hermes's is_camofox_mode()
# active, so the browser toolset drives Camoufox instead of a Chromium engine.
ENV CAMOFOX_URL=http://localhost:9377
ENTRYPOINT ["/scripts/entrypoint"]
EXPOSE 80
VOLUME ["/opt/config.yaml", "/opt/data"]
