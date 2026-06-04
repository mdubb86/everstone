set shell := ["bash", "-cu"]

IMAGE     := "everstone:dev"
DEV_NAME  := "everstone"
E2E_NAME  := "everstone-e2e"
DATA_DIR  := justfile_directory() + "/.everstone-data"
CONFIG    := justfile_directory() + "/config.yaml"

default:
    @just --list

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

# Open a shell inside the dev container
shell:
    docker exec -it {{DEV_NAME}} sh

# Restart the dev container (preserves data)
restart:
    docker restart {{DEV_NAME}}

# Stop the dev container (preserves data, keeps container)
stop:
    docker stop {{DEV_NAME}}

# Show s6 service status inside the dev container
status:
    docker exec {{DEV_NAME}} s6-rc -a list

# Generate an Obsidian LiveSync setup URI for this server's public_url
setup-livesync:
    docker exec -it {{DEV_NAME}} setup-obsidian-livesync

# Interactive Hermes Codex OAuth flow (one-time agent auth)
hermes-auth:
    docker exec -it -e HERMES_HOME=/opt/data/hermes {{DEV_NAME}} hermes auth add codex-oauth

# Remove the dev container (preserves data dir and image)
down:
    docker rm -f {{DEV_NAME}} 2>/dev/null || true
    docker rm -f {{E2E_NAME}} 2>/dev/null || true

# Snapshot /opt/data into the data dir's backups/ subfolder
backup:
    docker exec {{DEV_NAME}} /scripts/backup
    @ls -lh {{DATA_DIR}}/backups/ | tail -3

# Run the full e2e suite (builds + boots a throwaway container)
e2e: build
    cd e2e && uv run pytest -v

# Full clean: remove dev + e2e containers and image. Data dir NOT touched.
clean: down
    docker rmi {{IMAGE}} 2>/dev/null || true
    @echo "Data dir preserved at {{DATA_DIR}}"

# DESTRUCTIVE: stop container + wipe data dir. Notes, tasks, CouchDB — gone.
# After this, `just dev` boots a fresh empty container. You also need to delete
# the matching vault folder on every Mac/phone that was synced.
#
# Uses a privileged container to delete: CouchDB writes as uid 5984, so files
# end up owned by uid 5984 on the host bind-mount — your user can't rm them.
reset: down
    @echo ""
    @echo "  This will delete {{DATA_DIR}} (notes, tasks, CouchDB)."
    @echo "  Press Ctrl-C within 5 seconds to abort."
    @echo ""
    @sleep 5
    @if [ -d {{DATA_DIR}} ]; then \
        docker run --rm -v {{DATA_DIR}}:/wipe alpine sh -c 'find /wipe -mindepth 1 -delete' ; \
        rmdir {{DATA_DIR}} 2>/dev/null || true ; \
    fi
    @echo "  Wiped. Next: 'just dev' to start fresh."

# Internal: bail out unless config.yaml exists
_check-config:
    @test -f {{CONFIG}} || (echo "Missing config.yaml at {{CONFIG}}." && \
        echo "Copy config.example.yaml and fill it in:" && \
        echo "  cp config.example.yaml config.yaml" && exit 1)
