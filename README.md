# EverStone

A self-hosted, Telegram-native personal-assistant hub. One Docker container runs the
**Hermes** agent (Nous Research) as a Telegram gateway, alongside the services it needs
to manage your calendar, tasks, and notes:

- **Telegram** — you chat with the agent through your own private bot. Full tools in
  your DM; **tasks-only in any group chat**, enforced by a fail-closed access hook.
- **Google Calendar** (the `es_cal_*` tools) — read/write events across your calendars.
- **CalDAV tasks** via Radicale (the `es_tasks_*` tools) — to-dos, shopping lists,
  checklists, due dates, reminders, and one level of subtasks. Syncs with apps like Tasks.org.
- **Obsidian notes** (the `es_notes_*` tools) via CouchDB + the LiveSync bridge.
- **Caddy** reverse proxy and **s6** supervision tie it together.

The agent acts through a curated set of **MCP tools** (`es_*`) — not ad-hoc shell — so the
exposed tools are its capability boundary; its behavior is shaped by a small set of skills
(to-dos, shopping, checklists, calendar, note-taking).

## Install

See **[docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)** for the deployment runbook (built for
Unraid and plain `docker run` with bind mounts). Copy `config.example.yaml` to
`config.yaml`, fill in your values, and keep it local — it holds your secrets and is
gitignored.

## Architecture

- **[docs/architecture.md](docs/architecture.md)** — the design and the rationale
  behind the key decisions (the living source of truth).
- **[docs/hermes-integration.md](docs/hermes-integration.md)** — exactly what EverStone
  touches in the Hermes config versus what is yours to manage.

## Repository layout

| Path | What |
|---|---|
| `es/` | the `es` MCP server (FastMCP) — exposes the agent's `es_tasks_*` / `es_cal_*` / `es_notes_*` / `es_weather` / maps / contacts tools |
| `access_hook/` | the fail-closed `pre_tool_call` hook that gates tools by chat type |
| `scripts/` | setup / configure / admin (`esadmin`) + the Google OAuth helper |
| `services/` | s6 service definitions (Hermes gateway, CouchDB, Radicale, Caddy, …) |
| `config/` | Caddy config + the JSON schema for `config.yaml` |
| `docs/` | architecture, bootstrap runbook, Hermes config contract |
| `e2e/` | end-to-end tests (boot a throwaway container) |
| `Dockerfile`, `Justfile`, `build.sh` | image build + dev workflow |

## Status

A personal, self-hosted project, built around one operator's setup — expect to adapt
it to yours. Issues and PRs are welcome.

## License

[MIT](LICENSE) © 2026 Michael
