# EverStone ↔ Hermes — config contract (operator guide)

EverStone runs the **Hermes** agent as its brain. This documents exactly what
EverStone touches in the Hermes config and what is **yours** to manage — so you
have full flexibility without EverStone clobbering your settings.

The rule: **EverStone asserts the security floor + structural must-haves, seeds
the model once, and leaves everything else to you.**

## What EverStone asserts (every boot)

These are security/structural and are re-applied on each container start (from
`config.yaml`), so the bot can't be accidentally exposed or broken:

| Setting | Where it's written | Source |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | profile **`.env`** (it's a secret) | `config.yaml: telegram.bot_token` |
| `TELEGRAM_ALLOWED_USERS` | profile **`config.yaml`** (top-level; Hermes bridges it to the env var the gateway reads) | `config.yaml: telegram.owner_user_id` |
| `terminal.backend: local` | profile `config.yaml` | structural (the agent runs tools in-container) |

> The allowlist is fail-closed: if it were empty, Hermes denies everyone. That's
> why EverStone owns it — a misconfig would expose or brick the bot.

## What EverStone seeds once (first profile creation only)

| Setting | Source | After first boot |
|---|---|---|
| `model` / `provider` | `config.yaml: hermes.model` | **Yours.** Change anytime with `esadmin model` or `model.default` in the Hermes config — EverStone never re-asserts it. |

## What you own (EverStone never touches)

Everything else in your Hermes config — full flexibility:
- `model.default` / `model.provider` (after the one-time seed)
- `display.*` (verbosity, interim messages, tool progress)
- `reasoning_effort`, `agent.max_turns`, `agent.tool_use_enforcement`
- `curator.*`, `compression.*`
- `auxiliary.*` (incl. `auxiliary.vision`)
- Telegram **policy**: `messaging.telegram.unknown_user_action`,
  `group_trigger`, group allowlists (`platforms.telegram.extra.*` /
  `TELEGRAM_GROUP_ALLOWED_*`)

Set these with `esadmin config set …`, `esadmin model`, or by editing the Hermes
profile config directly. They survive reboots — EverStone won't overwrite them.

## What EverStone provides as infrastructure (not Hermes config)

- **`es`** — the agent tool-gateway CLI (`es cal`, `es tasks`); reads
  `config.yaml` directly.
- **access_hook** — the `pre_tool_call` plugin gating group chats to `es tasks`.
- **skills** (e.g. calendar), **`AGENTS.md`** (environment facts), and the
  gcal/CalDAV/`public_url` config consumed by `es`/auth/services.

## Where settings live (mental model)

- **EverStone `config.yaml`** (`/opt/config.yaml`) = EverStone-specific:
  `telegram.{bot_token, owner_user_id}`, `hermes.model` (seed), gcal creds,
  caldav, obsidian, `public_url`. EverStone derives the Hermes security keys from
  here.
- **Hermes profile config** (`$HERMES_HOME/profiles/everstone/{config.yaml,.env}`)
  = the live Hermes settings. EverStone writes only the asserted/seeded keys
  above; the rest is yours.

## Common operator tasks

- **Change the model:** `esadmin model` (or set `model.default` in the Hermes
  config). EverStone won't revert it.
- **Add another allowed Telegram user:** edit `telegram.owner_user_id`… (single
  owner today) — or for group access use the Telegram group allowlists. The DM
  allowlist EverStone asserts is the owner.
- **Tune verbosity / reasoning / curator:** `esadmin config set <key> <value>`.

## Note

A legacy `/opt/config/hermes/envdir` (+ a sourceable env file) is still generated
and loaded as a redundant safety net for the gateway and for `esadmin`/`auth`
tools. Retiring it (so the Hermes config file + `.env` are the sole source) is a
tracked follow-up in `docs/architecture.md`.
