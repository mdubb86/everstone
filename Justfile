set shell := ["bash", "-cu"]

IMAGE     := "everstone:dev"
DEV_NAME  := "everstone"
E2E_NAME  := "everstone-e2e"

# CONFIG and DATA_DIR default to workspace-local so cloning + `just dev`
# Just Works for someone hacking on EverStone. When developing inside an
# ephemeral SBX VM, set EVERSTONE_CONFIG / EVERSTONE_DATA_DIR (in
# /etc/sandbox-persistent.sh, or per-shell) to point at host-mounted
# directories so vault / CouchDB / agent state survive a VM rebuild.
#
# Both must be ABSOLUTE paths (docker bind mounts reject relative ones).
DATA_DIR  := env_var_or_default("EVERSTONE_DATA_DIR", justfile_directory() + "/.everstone-data")
CONFIG    := env_var_or_default("EVERSTONE_CONFIG", justfile_directory() + "/config.yaml")

default:
    @just --list

# ── Lifecycle (only useful WITH the source repo) ──────────────────────────

# Build the image
build:
    docker build -t {{IMAGE}} .

# Start the dev container on :80 (with persistent ./.everstone-data)
dev: build _check-config
    mkdir -p {{DATA_DIR}}
    docker rm -f {{DEV_NAME}} 2>/dev/null || true
    docker run -d \
        --name {{DEV_NAME}} \
        --restart unless-stopped \
        -p 80:80 \
        -v {{CONFIG}}:/opt/config.yaml:ro \
        -v {{DATA_DIR}}:/opt/data \
        {{IMAGE}}
    @sleep 3 && echo "" && curl -fsS http://localhost/health && echo "  ← /health reachable"

# Tail dev container logs (Ctrl-C to stop)
logs:
    docker logs -f {{DEV_NAME}}

# Open a bash shell inside the dev container (esadmin tab-completion works here).
shell:
    docker exec -it {{DEV_NAME}} bash

# Restart / stop / remove the dev container (data preserved on restart + stop)
restart:
    docker restart {{DEV_NAME}}
stop:
    docker stop {{DEV_NAME}}
down:
    docker rm -f {{DEV_NAME}} 2>/dev/null || true
    docker rm -f {{E2E_NAME}} 2>/dev/null || true

# Run the full e2e suite (builds + boots a throwaway container)
e2e: build
    cd e2e && uv run pytest -v

# Full clean: remove dev + e2e containers and image. Data dir NOT touched.
clean: down
    docker rmi {{IMAGE}} 2>/dev/null || true
    @echo "Data dir preserved at {{DATA_DIR}}"

# DESTRUCTIVE: wipe data dir + remove container. Prompts unless --yes is passed.
reset *FLAGS: down
    #!/usr/bin/env bash
    # Uses a privileged container to delete because CouchDB writes as uid 5984
    # and those files end up owned by 5984 on the host bind-mount — your user
    # can't rm them.
    set -euo pipefail
    if [[ " {{FLAGS}} " != *" --yes "* ]]; then
        read -r -p "Delete {{DATA_DIR}} (notes, tasks, CouchDB)? [y/N] " ans
        case "$ans" in
            y|Y|yes|YES) ;;
            *) echo "Aborted."; exit 1 ;;
        esac
    fi
    if [ -d {{DATA_DIR}} ]; then
        docker run --rm -v {{DATA_DIR}}:/wipe alpine sh -c 'find /wipe -mindepth 1 -delete'
        rmdir {{DATA_DIR}} 2>/dev/null || true
    fi
    echo "Wiped. Next: 'just dev' to start fresh."

# ── Agent CLI passthrough — the in-container `es` agent tool-gateway ──────
# `just es <args>` runs the AGENT's es CLI in the container (e.g. `es cal
# agenda …`, `es tasks list`) — exactly what the assistant uses. JSON output.
es +ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -t 0 ] && [ -t 1 ]; then DT="-it"; else DT="-i"; fi
    docker exec $DT {{DEV_NAME}} es {{ARGS}}

# ── Operator surface — the in-container `esadmin` CLI ─────────────────────
# `just esadmin --help` lists every operator subcommand (status, logs, restart,
# backup, sync-state, auth, session, setup, calendars, chat). -it vs -i adapts to
# a TTY so OAuth paste / chat get a full TTY while scripts/pipes don't break.
esadmin +ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -t 0 ] && [ -t 1 ]; then DT="-it"; else DT="-i"; fi
    docker exec $DT {{DEV_NAME}} esadmin {{ARGS}}

# Frequent operator shortcuts (muscle memory). Same as `just esadmin <verb>`.
chat:
    #!/usr/bin/env bash
    if [ -t 0 ] && [ -t 1 ]; then DT="-it"; else DT="-i"; fi
    docker exec $DT {{DEV_NAME}} esadmin chat
# Set the LLM model + run its provider auth (one-time). e.g. `just model openai-codex/gpt-5.5`.
model +ARGS:
    #!/usr/bin/env bash
    if [ -t 0 ] && [ -t 1 ]; then DT="-it"; else DT="-i"; fi
    docker exec $DT {{DEV_NAME}} esadmin model {{ARGS}}

# ── Internal ──────────────────────────────────────────────────────────────

# Bail out unless config.yaml exists
_check-config:
    @test -f {{CONFIG}} || (echo "Missing config.yaml at {{CONFIG}}." && \
        echo "Copy config.example.yaml and fill it in:" && \
        echo "  cp config.example.yaml config.yaml" && exit 1)
