# agentic-network

A shallow orchestrator-worker network of specialized AI agents that collaborate
to deliver software, under a single human owner. See [`CLAUDE.md`](CLAUDE.md) for
the architecture and [`docs/plan.md`](docs/plan.md) for the milestone plan.

Status: **Phase 0** — the state store (`store/`) and message contract
(`schemas/`) are in place. No agents yet.

## Quickstart

Install dependencies and create your env file:

```bash
uv sync
cp .env.example .env      # set POSTGRES_PASSWORD (the only required value)
```

Start the backing services (Postgres + Redis):

```bash
# Any machine with Docker:
docker compose up -d

# Or, in an environment without a Docker daemon (e.g. the web sandbox):
scripts/dev-services.sh up
```

Run the checks:

```bash
uv run pytest -q          # integration tests (auto-skip if no store; retries startup)
uv run ruff check .       # lint
uv run mypy store schemas # typecheck
```

You set the password in **one** place — `POSTGRES_PASSWORD`. Docker Compose
uses it for the server, and `store/db.py` builds the connection string from
the same `POSTGRES_*` vars, so the app and database can't drift. Set
`DATABASE_URL` only to point at an external/managed Postgres (it then wins).

## Layout

| Path | What it is |
|------|------------|
| `schemas/` | Typed inter-agent message contract (`TaskMessage`, roles, statuses) |
| `store/` | State store: Postgres task log + Redis work queue |
| `docs/` | Plan and deployment design |
| `scripts/` | Dev tooling (native Postgres/Redis for daemonless envs) |

Connection config is read from the environment (`POSTGRES_*`, optional
`DATABASE_URL` / `REDIS_URL`); see `.env.example`.
