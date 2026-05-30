# agentic-network

A shallow orchestrator-worker network of specialized AI agents that collaborate
to deliver software, under a single human owner. See [`CLAUDE.md`](CLAUDE.md) for
the architecture and [`docs/plan.md`](docs/plan.md) for the milestone plan.

Status: **Phase 0** — the state store (`store/`) and message contract
(`schemas/`) are in place. No agents yet.

## Quickstart

Install dependencies:

```bash
uv sync
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
uv run pytest -q          # integration tests (auto-skip if the store is down)
uv run ruff check .       # lint
uv run mypy store schemas # typecheck
```

## Layout

| Path | What it is |
|------|------------|
| `schemas/` | Typed inter-agent message contract (`TaskMessage`, roles, statuses) |
| `store/` | State store: Postgres task log + Redis work queue |
| `docs/` | Plan and deployment design |
| `scripts/` | Dev tooling (native Postgres/Redis for daemonless envs) |

Connection strings default to the local dev services and are read from
`DATABASE_URL` / `REDIS_URL` (see `.env.example`).
