# agentic-network

A shallow orchestrator-worker network of specialized AI agents that collaborate
to deliver software, under a single human owner. See [`CLAUDE.md`](CLAUDE.md) for
the architecture and [`docs/plan.md`](docs/plan.md) for the milestone plan.

Status: **Phase 1** — end-to-end feature delivery. A feature request becomes a
pull request: orchestrator → Designer → Engineer (writes code + tests) →
Reviewer gate → branch/commit/PR (optional merge). Triggered by CLI, polling,
or GitHub webhook. Live runs need `GITHUB_TOKEN` and `ANTHROPIC_API_KEY`.

## Quickstart

One command — installs deps, starts Postgres + Redis, applies the schema:

```bash
make dev
```

It uses Docker if a daemon is available and falls back to native servers
(`scripts/dev-services.sh`) in environments without one, like the web sandbox.
Then run the full gate:

```bash
make check                # lint + typecheck + tests
```

**Port already in use?** If `5432` (or `6379`) is taken — e.g. you already run
Postgres locally — set `POSTGRES_PORT` (and/or `REDIS_PORT`) in `.env` to a
free port and re-run `make dev`. That one var drives both the published
container port and what the app connects to, so nothing else needs changing.

`make help` lists every target. The manual equivalents, if you'd rather not
use `make`:

```bash
uv sync
cp .env.example .env      # set POSTGRES_PASSWORD (the only required value)
docker compose up -d      # or: scripts/dev-services.sh up
uv run pytest -q          # lint: ruff check .   types: mypy store schemas
```

You set the password in **one** place — `POSTGRES_PASSWORD`. Docker Compose
uses it for the server, and `store/db.py` builds the connection string from
the same `POSTGRES_*` vars, so the app and database can't drift. Set
`DATABASE_URL` only to point at an external/managed Postgres (it then wins).

## Running a workflow (live)

Live runs need a GitHub PAT and an Anthropic key:

```bash
export GITHUB_TOKEN=ghp_...        # repo scope
export ANTHROPIC_API_KEY=sk-ant-...

make serve                          # orchestrator + worker pool (long-running)
make api                            # trigger/webhook API on :8000 (separate shell)
```

Start a run three ways:

```bash
# 1. CLI
uv run python -m triggers.cli --owner you --repo your-repo \
  --title "Add a /health endpoint" --body "return 200 OK" --base main

# 2. API ("add feature X to repo Y")
curl -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"owner":"you","repo":"your-repo","title":"Add /health","body":"return 200"}'

# 3. GitHub webhook — point a repo's webhook at POST /webhook/github
#    (issues 'opened' events create runs). Polling is the fallback.
```

## Layout

| Path | What it is |
|------|------------|
| `schemas/` | Typed inter-agent message contract (`TaskMessage`, roles, statuses) |
| `store/` | State store: Postgres task log + Redis work queue |
| `agents/` | Designer, Engineer, Reviewer + LLM/executor seams |
| `orchestrator/` | Run state machine + worker |
| `github/` | GitHub REST client (issues, branches, commits, PRs, merge) |
| `triggers/` | CLI, poller, webhook/API |
| `app/` | Config + long-running service wiring |
| `docs/` | Plan and deployment design |
| `scripts/` | Dev tooling (native Postgres/Redis for daemonless envs) |

Connection config is read from the environment (`POSTGRES_*`, optional
`DATABASE_URL` / `REDIS_URL`); see `.env.example`.
