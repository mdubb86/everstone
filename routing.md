# EverStone Routing Documentation

> **Design status:** The current EverStone design is the Hermes hub described
> in [`docs/superpowers/specs/2026-06-03-everstone-hermes-design.md`](docs/superpowers/specs/2026-06-03-everstone-hermes-design.md).
> The previous `radfire` / `taskite` bidirectional bridge has been removed —
> CalDAV is now stock Radicale exposed at `/caldav`, and the agent
> (`everstone_tasks` MCP) is the only thing that writes to it. The Git HTTP
> backend has likewise been removed; backups are tar.gz snapshots of
> `/opt/data` produced by `scripts/backup`.

## Overview

EverStone uses Caddy as a reverse proxy to route requests to different backend services using subpath-based routing. All services are accessible through a single domain with different URL paths.

## Architecture

```
Client Request → Caddy (Port 80) → Backend Services
                   ├─ /health     → Health check endpoint
                   ├─ /db/*       → CouchDB (Port 5984)
                   ├─ /caldav/*   → Radicale (Port 5232)
                   └─ /*          → Default response / Future bridge UI
```

> **Git HTTP backend removed** — backups are tar.gz snapshots of /opt/data (see scripts/backup).

## Service Endpoints

### CouchDB (Obsidian LiveSync)
- **External URL**: `http://everstone.home/db/`
- **Backend**: `localhost:5984`
- **Path handling**: Caddy strips `/db` prefix before forwarding
- **Example**: `http://everstone.home/db/my_vault/doc123` → `http://localhost:5984/my_vault/doc123`

### Radicale (CalDAV)
- **External URL**: `http://everstone.home/caldav/`
- **Backend**: `localhost:5232`
- **Path handling**: `X-Script-Name: /caldav` header tells Radicale about subpath
- **Example**: `http://everstone.home/caldav/user/calendar` → Radicale handles with subpath awareness

### Health Check
- **External URL**: `http://everstone.home/health`
- **Response**: "OK" 200
- **Purpose**: Container health monitoring

## Why Subpath Routing?

We chose subpath routing over subdomain routing for several reasons:

1. **Single domain management** - Only need one domain and one SSL certificate
2. **No DNS configuration** - No need for wildcard DNS or multiple A records
3. **Simpler certificate management** - One cert for `everstone.home` instead of multiple
4. **Official support** - Both CouchDB and Radicale officially support subpath proxying
5. **CORS benefits** - All services under same origin, avoiding cross-origin issues

## Technical Details

### CouchDB Subpath Configuration

CouchDB officially supports subpath proxying with Caddy. The configuration automatically:
- Strips the `/db` prefix before forwarding requests
- Handles URL rewriting in responses
- Maintains correct redirect behavior

**Critical requirement**: `flush_interval -1` disables response buffering, which is **essential** for:
- Continuous replication (used by Obsidian LiveSync)
- Changes feed (used by our Python bridge service)
- Real-time data synchronization

Without disabling buffering, the continuous HTTP streams will break.

**Official documentation**: [CouchDB Reverse Proxies - Caddy Subdirectory](https://docs.couchdb.org/en/stable/best-practices/reverse-proxies.html#reverse-proxying-couchdb-in-a-subdirectory-with-caddy-2)

### Radicale Subpath Configuration

Radicale handles subpath routing through the `X-Script-Name` header. This tells Radicale:
- What URL prefix to expect in requests
- How to construct URLs in responses (XML, redirects, etc.)
- Where to look for WebDAV resources

The header approach is more flexible than path stripping because Radicale's CalDAV/CardDAV responses contain XML with embedded URLs that need to be aware of the subpath.

**Configuration reference**: Radicale reads `X-Script-Name` and adjusts all generated URLs accordingly.

## Caddy Configuration

Located in: `/scripts/setup_caddy`

Key configuration patterns:

### handle_path (for CouchDB)
```caddy
handle_path /db/* {
    reverse_proxy localhost:5984 {
        flush_interval -1  # Disable buffering
        header_up Host {upstream_hostport}
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

`handle_path` automatically strips the `/db` prefix before proxying.

### handle (for Radicale)
```caddy
handle /caldav/* {
    reverse_proxy localhost:5232 {
        header_up X-Script-Name /caldav
        header_up Host {upstream_hostport}
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

`handle` matches the path but doesn't strip it - Radicale handles the subpath internally via the header.

## Headers Explained

### Standard Proxy Headers

- **Host**: Preserves the original host header for backend services
- **X-Real-IP**: Client's actual IP address (single IP)
- **X-Forwarded-For**: Chain of proxy IPs (can be multiple)
- **X-Forwarded-Proto**: Original protocol (http/https) for proper redirect generation

### Service-Specific Headers

- **X-Script-Name** (Radicale only): Tells the service what URL prefix it's mounted at

## Client Configuration

### Obsidian LiveSync Plugin

Configure with the `/db` subpath included:

```
Remote CouchDB URI: http://everstone.home/db
Remote Database name: my_vault
Username: admin
Password: [your password]
```

The plugin will make requests to `http://everstone.home/db/my_vault/...`

### Tasks.org (CalDAV)

Configure with the `/caldav` subpath:

```
Server URL: http://everstone.home/caldav/username/calendar
Username: username
Password: [your password]
```

CalDAV clients typically discover the full path automatically via `.well-known` endpoints.

## Alternative Approaches Considered

### Subdomain Routing
```
db.everstone.home → CouchDB
caldav.everstone.home → Radicale
```

**Pros**:
- Cleaner separation
- No path rewriting needed
- Each service "owns" its domain

**Cons**:
- Requires wildcard DNS or multiple DNS entries
- Requires wildcard cert or multiple certs
- More complex DNS/cert management
- NPM (Nginx Proxy Manager) doesn't support wildcard routing

**Verdict**: More overhead for minimal benefit

### Root Path Routing
```
everstone.home/ → CouchDB
everstone.home/caldav → Radicale
```

**Pros**:
- CouchDB at root (no subpath issues)
- One less path segment

**Cons**:
- CouchDB "owns" the root domain
- Can't add web UI or other services at root
- Less flexible for future additions

**Verdict**: Too inflexible for a multi-service platform

## Troubleshooting

### CouchDB Continuous Replication Not Working

**Symptom**: Obsidian LiveSync sync gets stuck or times out

**Cause**: Response buffering is enabled

**Solution**: Verify `flush_interval -1` is set in Caddy config for CouchDB proxy

### Radicale URLs Are Incorrect

**Symptom**: CalDAV client receives URLs without `/caldav` prefix

**Cause**: `X-Script-Name` header is missing

**Solution**: Verify `header_up X-Script-Name /caldav` is set in Caddy config

### 404 Not Found Errors

**Symptom**: Requests to `/db/...` return 404

**Cause**: Backend service not running or path misconfigured

**Solution**:
1. Check if CouchDB is running: `curl localhost:5984`
2. Check Caddy logs for routing issues
3. Verify `handle_path` pattern matches your URL structure

## Future Expansion

The root path (`/*`) is currently serving a simple "EverStone Server" response. This can be replaced with:

- **Bridge Service Web UI**: Admin interface for the Python sync bridge
- **Status Dashboard**: Monitoring for all services
- **API Endpoints**: Bridge service REST API for configuration
- **Static Content**: Documentation, help pages, etc.

Since the bridge service will be written in Python, it can easily serve a web UI (using Flask, FastAPI, etc.) at the root while Caddy proxies `/db` and `/caldav` to their respective backends.

## References

- [CouchDB Reverse Proxy Best Practices](https://docs.couchdb.org/en/stable/best-practices/reverse-proxies.html)
- [CouchDB Caddy Subdirectory Configuration](https://docs.couchdb.org/en/stable/best-practices/reverse-proxies.html#reverse-proxying-couchdb-in-a-subdirectory-with-caddy-2)
- [Radicale Documentation](https://radicale.org/v3.html)
- [Caddy Reverse Proxy Documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Obsidian LiveSync Setup Guide](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/setup_own_server.md)

## Version History

- **2025-11-23**: Initial routing configuration with CouchDB and Radicale subpath support
