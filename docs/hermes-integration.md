# EverStone ↔ Hermes — config contract (operator guide)

EverStone runs the **Hermes** agent as its brain. This documents exactly what
EverStone touches in the Hermes config and what is **yours** to manage — so you
have full flexibility without EverStone clobbering your settings.

The rule: **EverStone asserts the security floor + structural must-haves, and
leaves everything else to you.**

## What EverStone asserts (every boot)

These are security/structural and are re-applied on each container start (from
`config.yaml`), so the bot can't be accidentally exposed or broken. The two
Telegram values are enforced with **VERIFY + LOUD-FAIL on drift**: if the live
Hermes profile differs from `config.yaml`, boot logs a clear error (via
`scripts/assert_telegram.py`) — `config.yaml` is authoritative and divergence is
never silently overwritten.

| Setting | Where it's written | Source |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | profile **`.env`** (it's a secret) | `config.yaml: telegram.bot_token` |
| `TELEGRAM_ALLOWED_USERS` | profile **`config.yaml`** (top-level; Hermes bridges it to the env var the gateway reads) | `config.yaml: telegram.owner_user_id` |
| `terminal.backend: local` | profile `config.yaml` | structural (the agent runs tools in-container) |

> The allowlist is fail-closed: if it were empty, Hermes denies everyone. That's
> why EverStone owns it — a misconfig would expose or brick the bot.

## What you set once (one-time operator action, not seeded from config.yaml)

The LLM model and provider are **not** in `config.yaml` and are **not** seeded
by EverStone. You set them once with a single command that also runs provider
auth:

```bash
just model openai-codex/gpt-5.5
```

This calls `esadmin model <value>`, which sets the model+provider in the Hermes
profile **and** runs the provider's auth flow in one step. Run it after first
boot and whenever you want to switch models. EverStone never re-asserts or
overwrites it — it is yours from that point on.

## What you own (EverStone never touches)

Everything else in your Hermes config — full flexibility:
- `model.default` / `model.provider` (set once via `just model`, then yours)
- `display.*` (verbosity, interim messages, tool progress)
- `reasoning_effort`, `agent.max_turns`, `agent.tool_use_enforcement`
- `curator.*`, `compression.*`
- `auxiliary.*` (incl. `auxiliary.vision`)
- Telegram **policy**: `messaging.telegram.unknown_user_action`,
  `group_trigger`, group allowlists (`platforms.telegram.extra.*` /
  `TELEGRAM_GROUP_ALLOWED_*`)

Set these with `hermes -p everstone config set …`, `hermes -p everstone model`, or by editing the Hermes
profile config directly. They survive reboots — EverStone won't overwrite them.

## What EverStone provides as infrastructure (not Hermes config)

- **`es`** — the agent tool-gateway CLI (`es cal`, `es tasks`); reads
  `config.yaml` directly.
- **access_hook** — the `pre_tool_call` plugin gating group chats to `es tasks`.
- **skills** (e.g. calendar), **`AGENTS.md`** (environment facts), and the
  gcal/CalDAV/`public_url` config consumed by `es`/auth/services.

## Where settings live (mental model)

- **EverStone `config.yaml`** (`/opt/config.yaml`) = EverStone-specific:
  `telegram.{bot_token, owner_user_id}`, gcal creds, caldav, obsidian,
  `public_url`. **No `hermes.model`** — the LLM model is not stored here.
  EverStone derives the Hermes security keys (the two Telegram values) from here
  and asserts them on every boot.
- **Hermes profile config** (`$HERMES_HOME/profiles/everstone/{config.yaml,.env}`)
  = the live Hermes settings. EverStone writes only the asserted keys above; the
  rest (including model+provider, set via `just model`) is yours.

## Common operator tasks

- **Set or change the model:** `just model <provider/model>` (e.g.
  `just model openai-codex/gpt-5.5`). This runs `esadmin model`, which sets
  model+provider in the Hermes profile and re-runs provider auth. EverStone won't
  revert it.
- **Add another allowed Telegram user:** edit `telegram.owner_user_id`… (single
  owner today) — or for group access use the Telegram group allowlists. The DM
  allowlist EverStone asserts is the owner.
- **Tune verbosity / reasoning / curator:** `hermes -p everstone config set <key> <value>`.

> Run the `hermes -p everstone …` commands inside the container — `just shell`
> then `hermes -p everstone …`, or `docker exec -it everstone hermes -p everstone …`.

## Config source of truth

`config.yaml` (the Hermes profile config at
`/opt/data/hermes/profiles/everstone/config.yaml`) is the **sole source** for the
asserted keys. The legacy `/opt/config/hermes/envdir` envdir was retired — it no
longer exists and is not generated.
