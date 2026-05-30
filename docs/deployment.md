# Deployment

How to deploy the agentic network on a single VPS, with a web dashboard,
many concurrent workflows, real-time visibility, and pause/kill control.

## TL;DR

Docker Compose on one VPS. Six services: `caddy` (TLS proxy), `api`
(dashboard + control plane), `orchestrator` (drives workflows),
`worker` (scaled pool, runs agents), `postgres` (state), `redis`
(queue + live events). The Engineer worker spins up an **ephemeral Docker
sandbox container per code task** and tears it down on exit. Start here;
move to multi-host / managed services only when one box hurts.

## Reconciling "ephemeral agents" with "always-on dashboard"

plan.md says prefer ephemeral workers that spin up per task and exit. Your
requirements (many parallel runs, background orchestration, a real-time
dashboard, pause/kill mid-run) need things that are listening continuously.
We resolve this by splitting on what's actually ephemeral:

| Layer | Lifecycle | Why |
|-------|-----------|-----|
| API + dashboard | Always-on service | Must serve the UI and accept control commands at any time |
| Orchestrator | Always-on service | Watches for new runs, advances task graphs in the background |
| Worker pool | Long-running processes, one task at a time | Avoids paying container-spawn latency on every Claude call; scale by replica count |
| **Code sandbox** | **Ephemeral container per Engineer task** | This is the part that runs untrusted generated code — it spins up, runs tests, and is destroyed |
| Postgres / Redis | Always-on stateful services | Source of truth + queue must persist across everything |

So "ephemeral per task" stays true exactly where it earns its keep — the
code sandbox — while coordination and control live in always-on services.
The worker *processes* are long-running but **stateless**: all task state is
in Postgres, so any worker can pick up any task and a crashed worker loses
nothing.

## Architecture on one VPS

```
                          Internet
                             │
                      ┌──────▼──────┐
                      │   caddy     │  TLS (Let's Encrypt), reverse proxy
                      └──────┬──────┘
                             │
                      ┌──────▼──────┐   REST + WebSocket
                      │     api     │   - start/pause/kill runs
                      │  (FastAPI)  │   - query traces, costs
                      │  + dashboard│   - live event stream → browser
                      └──┬───────┬──┘
              writes runs│       │subscribes
                 control │       │run:{id}:events (pub/sub)
                  ┌──────▼──┐  ┌─▼─────┐
                  │ postgres│  │ redis │  task_queue + pub/sub + control flags
                  └──▲───▲──┘  └─▲───▲─┘
        claims runs  │   │       │   │ enqueue/dequeue tasks
              ┌──────┴┐  │       │   │
              │ orch- │  │ reads/│   │
              │ estra-│  │ writes│   │
              │ tor   │──┘ tasks └───┤ publishes events
              └───────┘              │
                              ┌──────┴───────┐  worker pool (scale: N)
                              │   worker ×N  │  pull task → run agent →
                              │              │  write result → publish event
                              └──────┬───────┘
                                     │ docker run (per Engineer task)
                              ┌──────▼───────┐
                              │   sandbox    │  ephemeral, no network,
                              │  (per task)  │  read-only repo, runs tests
                              └──────────────┘
```

## Services (`docker-compose.yml`)

- **caddy** — reverse proxy + automatic HTTPS. Terminates TLS, forwards to
  `api`. (nginx + certbot is a fine substitute.)
- **api** — FastAPI app. Serves the dashboard SPA, exposes REST
  (`POST /runs`, `POST /runs/{id}/pause`, `POST /runs/{id}/kill`,
  `GET /runs/{id}`, `GET /runs/{id}/trace`) and a WebSocket
  (`/runs/{id}/stream`) that relays Redis pub/sub events to the browser.
  Stateless; scale if the dashboard gets heavy.
- **orchestrator** — long-running loop. Claims new runs from Postgres
  (via `SELECT ... FOR UPDATE SKIP LOCKED`), builds the task graph,
  enqueues tasks to Redis, advances workflow state as task results land,
  respects run-level pause/kill. Safe to run 1 replica to start; can run
  several with row-locking for HA.
- **worker** — the agent runtime, scaled to N replicas (`docker compose up
  --scale worker=8`). Each pulls one task, dispatches to the right agent
  (Orchestrator-step / Designer / Engineer / Reviewer logic), calls the
  Claude API, writes results, publishes events. The Engineer path launches
  a sandbox container for code execution.
- **postgres** — state store: `runs`, `tasks`, `task_graph`, `control`.
  Named volume for durability.
- **redis** — `task_queue` (work distribution), pub/sub channels
  (`run:{id}:events` for the live feed), and `control:{run_id}` flags.

## Control plane (pause / kill)

State lives in Postgres (durable) and is mirrored to Redis (fast reads):

- `runs.control` ∈ `{ running, pause_requested, paused, kill_requested,
  killed }`.
- **Pause**: API sets `pause_requested`. The orchestrator stops enqueuing
  new tasks for that run; in-flight workers finish their current step,
  then hold. Status → `paused`. Resume flips it back to `running`.
- **Kill**: API sets `kill_requested`. Orchestrator stops the run; workers
  check the flag **at safe boundaries** (between steps, before each Claude
  call) and abort, marking their task `killed`. Any live sandbox container
  for that run is `docker kill`ed. Status → `killed`.
- Workers must check control flags at boundaries — there's no preempting a
  Claude call mid-flight, so kill latency is "current step," not instant.
  This is the honest tradeoff; budget timeouts keep steps bounded.

## Real-time dashboard feed

Durable state in Postgres; the **live** feed rides Redis pub/sub:

1. Workers and orchestrator publish JSON events to `run:{id}:events`
   (task started, tokens spent, step done, gate passed/failed).
2. `api` subscribes and forwards over the run's WebSocket to the browser.
3. Dashboard renders task-graph progress, per-task cost/latency, and a log
   tail. On reconnect it backfills from Postgres, then resumes the stream.

## Post-run report

When a run terminates, the orchestrator writes a summary row and emits a
trace artifact (JSON lines of every task: agent, input/output refs, tokens,
latency, gate results) plus a cost summary (tokens and $ per agent, per
run). The dashboard exposes both for download; archive them to object
storage for history.

## VPS sizing (starting point)

- **2–4 vCPU, 8–16 GB RAM** handles a modest worker pool plus Postgres +
  Redis. Agents are I/O-bound on the Claude API, not CPU — concurrency is
  capped by your API rate limits and budget, not the box, until sandbox
  test runs get heavy.
- Disk: 40–80 GB SSD (Postgres + traces + container images).
- Scale workers with replica count; watch Claude API rate limits first —
  that's the real ceiling, not the VPS.

## Sandbox isolation (read this — it's the security crux)

The Engineer's sandbox runs **untrusted, LLM-generated code**. How a worker
launches that container matters:

- **Don't** bind-mount `/var/run/docker.sock` into a worker casually —
  socket access ≈ root on the host. If you do, treat the worker as
  privileged and keep it minimal.
- **Recommended v1**: a dedicated sandbox runner using a hardened runtime
  (gVisor / `runsc`, or Firecracker via Kata) so generated code can't
  escape to the host.
- **Defaults for every sandbox container**: `--network none`, read-only
  repo mount, writable scratch tmpfs only, dropped capabilities, CPU/mem/
  pid limits, and a wall-clock timeout. Destroy on exit.
- Generated code is **never** auto-merged — it lands in a PR for human
  review (this is already a workflow gate, and it's also your last security
  backstop).

## Secrets & config

- `ANTHROPIC_API_KEY`, GitHub token, Postgres/Redis creds via Docker
  secrets or a root-only `.env` — never baked into images, never committed.
- Per-task budgets (max tokens, timeout) and pool size in config, not code,
  so you can tune without redeploying.

## Backups & ops

- `pg_dump` on a cron to object storage; test a restore before you rely on
  it. Postgres is the source of truth — losing it loses run history and any
  paused runs.
- Redis is mostly reconstructable (queue + ephemeral events); enable AOF if
  you want in-flight queue durability across a Redis restart.
- Centralize logs (Loki, or just `docker compose logs` shipped off-box).
  Per CLAUDE.md: if a behavior can't be traced, it isn't done.

## Deploy steps (first cut)

1. Provision the VPS; install Docker + Compose; lock down SSH/firewall
   (only 80/443 public; Postgres/Redis stay on the internal network).
2. Point a domain at it; let Caddy fetch TLS certs.
3. Drop secrets into Docker secrets / `.env`.
4. `docker compose up -d` (postgres, redis, api, orchestrator,
   `--scale worker=N`, caddy).
5. Run DB migrations (schema from plan.md Phase 0).
6. Open the dashboard, start a run against a test issue, watch it stream,
   exercise pause/kill, confirm the PR + post-run report.

## When to outgrow this

A single VPS with Compose is right until one of these bites:

- Worker pool needs more than one box → move workers to a second host,
  keep one shared Postgres/Redis.
- Postgres becomes the bottleneck or you want managed backups/HA → managed
  Postgres (RDS / Cloud SQL) + managed Redis.
- You need autoscaling on bursty load or self-healing across many nodes →
  *then* consider k8s — not before. Per CLAUDE.md, no Kubernetes or service
  mesh without a demonstrated scaling need.

---

*This is the v1 deployment shape. Revisit after the first end-to-end run on
the VPS, when real cost/latency/concurrency numbers replace these estimates.*
