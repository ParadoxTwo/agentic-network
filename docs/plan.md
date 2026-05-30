# Plan: Agentic Organization — First Milestone

## Goal
Build ONE end-to-end workflow: **feature request → design → implement → review → tests pass → PR**.

Deliverable: a runnable system that takes a GitHub issue (feature request), orchestrates a team of specialized agents to design, code, and review it, and opens a PR to main. Verification gates at each stage. Full tracing and cost accountability.

## Scope

**In scope for v1:**
- One orchestrator agent (task decomposition, delegation, synthesis)
- Three specialist workers:
  - **Designer**: breaks down requirements, proposes architecture
  - **Engineer**: implements in code, writes tests
  - **Reviewer**: validates output against rubric (code style, test coverage, design consistency)
- External state store (Postgres + Redis)
- Verification gates (tests pass, linter clean, reviewer approval)
- GitHub integration (read issues, open PRs)
- Execution tracing (task log with who did what, tokens, latency, cost)
- Docker Compose dev setup

**Out of scope for v1:**
- Full org chart (no CEO, CTO, HR agents yet)
- Persistent per-role services (ephemeral workers)
- Horizontal team communication (coordinate through the store, not peer chat)
- Advanced features (auto-retry, branching strategies, incident response)

## Architecture

### Agents
All agents are ephemeral functions; they process a task and exit.

**Orchestrator** (Claude Opus 4.8)
- Objective: decompose a GitHub issue into a design task and engineering task; route to specialists; verify and synthesize results.
- Input: `{ issue_id: int, issue_title: str, issue_body: str }`
- Output: `{ pr_url: str, cost_tokens: int, latency_ms: int, trace: [...] }`
- Tools: GitHub API read (issues), task queue write, task log write
- Context: issue text only; does NOT see code or design details (workers own those)
- Termination: PR created or max 6 hops

**Designer** (Claude Sonnet 4.6)
- Objective: read a feature request and propose architecture (file structure, key functions, interfaces).
- Input: `{ issue_id: int, issue_title: str, issue_body: str }`
- Output: `{ architecture: str, file_structure: str, key_interfaces: str, decisions_log: str }`
- Tools: task log write; GitHub API read (linked PRs/issues for context if needed)
- Context: NO access to existing code yet
- Termination: architecture written or max 2 attempts

**Engineer** (Claude Sonnet 4.6)
- Objective: implement the design and write passing tests.
- Input: `{ design: str, file_structure: str, issue_id: int }`
- Output: `{ code_files: [{ path: str, content: str }], test_files: [...], test_results: str, decisions_log: str }`
- Tools: code sandbox (read repo, write/test code in isolation), task log write
- Context: design spec + existing codebase (read-only)
- Termination: all tests pass or max 3 attempts

**Reviewer** (Claude Opus 4.8)
- Objective: validate design, code, and tests against a rubric. Approve or reject with detailed feedback.
- Input: `{ design: str, code_files: [...], test_results: str, rubric: {...} }`
- Output: `{ status: "approved" | "rejected", feedback: str, requested_changes: [str] }`
- Tools: code sandbox (read code, run test validation), task log write
- Context: full design + code + tests (read-only)
- Termination: approval or rejection (1 pass)

### State & Coordination
**Postgres schema:**
- `tasks`: task_id, workflow_id, agent, status, input (JSON), output (JSON), started_at, completed_at, cost_tokens, notes
- `task_graph`: task_id, parent_task_id, status (waiting, in_progress, done, failed), ordered sequence of agent work
- `runs`: run_id, issue_id, status, created_by (human owner), created_at, final_pr_url, total_cost

**Redis queue:**
- `task_queue`: each task is a JSON message with task_id, agent_role, input, expected_output_schema

**External state:**
- GitHub: source of truth for issues; agents read and write PRs only through GitHub API (no local mirror)

### Message Schema
All inter-agent communication goes through the task store:
```json
{
  "task_id": "uuid",
  "workflow_id": "uuid", 
  "sender": "orchestrator",
  "recipient": "designer",
  "inputs": { "issue_id": 123, "issue_title": "...", "issue_body": "..." },
  "expected_output": { "architecture": "str", "file_structure": "str", ... },
  "max_steps": 2,
  "timeout_ms": 300000,
  "created_at": "2025-05-30T...",
  "status": "waiting" | "in_progress" | "done" | "failed"
}
```

Agents poll the task queue, process, write results to the task record, and exit. No agent-to-agent direct calls.

### Verification Gates
1. **Designer output**: architecture must be parseable (JSON schema validation), must reference file structure, no hallucinatory imports
2. **Engineer output**: tests must pass locally; code must parse (Python AST); linter must be clean
3. **Reviewer gate**: must approve explicitly; rejection routes the task back to engineer with feedback; max 1 reviewer rejection per engineer attempt

Failed gates write detailed failure reason to the task log and route the task back to the upstream agent.

### Execution Tracing
Every task record includes:
- `task_id`, `agent`, `status`, `input`, `output`
- `created_at`, `started_at`, `completed_at`
- `cost_tokens` (input + output token count from Claude API)
- `error` (if failed)
- `attempt_count`, `max_attempts`

At the end of the workflow, the orchestrator synthesizes a run summary:
```json
{
  "run_id": "uuid",
  "issue_id": 123,
  "status": "success" | "failed",
  "pr_url": "https://github.com/...",
  "tasks": [ { agent, status, cost_tokens, latency_ms }, ... ],
  "total_cost_tokens": 50000,
  "total_latency_ms": 180000,
  "trace_url": "internal task log"
}
```

## Implementation Plan

### Phase 0: Scaffolding (this week)
1. Set up Python 3.12 + uv + pytest
2. Initialize Postgres schema (tasks, task_graph, runs)
3. Initialize Redis connection
4. Draft the task message schema (JSON schema)
5. Create task queue client (enqueue, dequeue, mark_done, mark_failed)
6. Create task log writer (append to tasks table)
7. Write integration tests for store ops

**Deliverable**: a working `store/` module that orchestrator and agents can import.

### Phase 1: Orchestrator + GitHub integration
1. Build the Orchestrator agent (Claude SDK)
   - Input: GitHub issue
   - Decomposes to Designer task and Engineer task
   - Polls task queue for Designer and Engineer results
   - Passes Engineer output to Reviewer
   - Synthesizes run summary and creates PR
2. Add GitHub API client
   - Read issues
   - Create PRs
   - Update PR description with trace
3. Write orchestrator tests (mock agents, verify task graph)

**Deliverable**: orchestrator can read an issue, create a task graph, and verify completion.

### Phase 2: Designer agent
1. Build Designer (Claude Sonnet, Code Interpreter capable)
   - Reads issue from task input
   - Proposes architecture and file structure
   - Validates output against schema
   - Writes task record with output
2. Test with sample issues

**Deliverable**: designer produces valid architecture specs.

### Phase 3: Engineer agent
1. Build Engineer (Claude Sonnet + code sandbox)
   - Reads design from task input
   - Clones repo into sandbox
   - Implements code from design spec
   - Runs tests locally
   - Writes passing test output to task record
   - Handles test failures with retry logic
2. Sandbox setup:
   - Docker container with Python + test framework
   - Read-only repo mount
   - Write-only temp directory for code
   - No network access (or controlled GitHub API access only)

**Deliverable**: engineer produces working code with passing tests.

### Phase 4: Reviewer agent + verification
1. Build Reviewer (Claude Opus)
   - Reads design, code, test results
   - Validates against rubric:
     - Code follows linting rules
     - Test coverage >80%
     - Design rationale is clear
     - No security red flags
   - Approves or rejects with feedback
2. If rejected, route back to Engineer with specific feedback
3. Write reviewer tests (valid/invalid code samples)

**Deliverable**: reviewer can validate and reject output; orchestrator routes to retry.

### Phase 5: Integration + hardening
1. End-to-end test: issue → PR
2. Add cost budgets (max tokens per task, per run)
3. Add timeout budgets (max latency per agent)
4. Implement observability:
   - Trace export (JSON lines for analysis)
   - Cost dashboard (tokens/run)
5. Docker Compose: orchestrator, store (Postgres + Redis), agents as batch jobs
6. README with setup + running a workflow

**Deliverable**: working end-to-end system; costs and latencies visible; reproducible from source.

## Stack Decisions

- **Language/runtime**: Python 3.12 + uv for fast, reproducible dependency mgmt
- **Orchestration**: Claude Agent SDK + custom orchestrator (no framework; maximum control)
- **State store**: Postgres (source of truth for task log + runs) + Redis (task queue)
- **Code sandbox**: Docker (one ephemeral container per Engineer task)
- **Models**: 
  - Orchestrator, Reviewer: Claude Opus 4.8 (strongest reasoning, veto authority)
  - Designer, Engineer: Claude Sonnet 4.6 (fast, capable enough for specialized work)
- **Testing**: pytest + mock agents (mock Designer/Engineer/Reviewer to test orchestrator in isolation)
- **Containerization**: Docker Compose for dev; no k8s yet

## Success Criteria for v1

- [ ] Issue opened on GitHub
- [ ] Orchestrator reads issue, creates task graph
- [ ] Designer produces architecture spec (validated schema)
- [ ] Engineer implements code with passing tests
- [ ] Reviewer approves or rejects with feedback
- [ ] PR created to main with code + tests + trace
- [ ] Full trace logged and queryable
- [ ] Total workflow cost < $X (TBD: budget)
- [ ] Total workflow latency < 10 min (end-to-end)
- [ ] Documented setup instructions + one successful demo run

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Agent output format doesn't match schema | Validate early (phase 1); use structured output in Claude API |
| Agent retries spiral (engineer gets feedback, fails again) | Hard cap on retries (3 engineer attempts); escalate to human review if failed |
| Token cost explodes | Instrument early; set budgets per agent per task; log every call |
| Orchestrator loses task state (crash mid-run) | All state in Postgres; orchestrator restarts and resumes from task_id |
| Code sandbox escape / untrusted code | Ephemeral container; no network; dropped on exit; audit generated code before merge to main |
| GitHub API rate limits | Cache issue data; use conditional requests; retry with backoff |

## Next Steps

1. Confirm stack decisions (if different, update above).
2. Initialize repo with uv + pytest + schema definitions.
3. Start Phase 0 (store scaffolding).
4. Iterate: demo each phase end-to-end before moving to the next.

---

## Appendix: Why This Scope

**Why start with software delivery?** You said "software delivery first." The workflow is realistic (matches real PR lifecycle), verifiable (tests pass/fail are unambiguous), and lets you validate the whole system end-to-end without inventing work.

**Why shallow orchestration?** A 6-layer hierarchy means at least 6 LLM calls of reformatting and forwarding before real work happens. A single orchestrator delegates to specialists and synthesizes results. No CEO or middle managers yet.

**Why ephemeral agents?** Persistent services per role require orchestration, service discovery, secrets, health checks, and observability ops that dwarf the agents themselves. Ephemeral workers pulling from a queue are cheaper, simpler, and horizontally scalable. Earn persistence only when a role genuinely needs it (e.g., a webhook listener).

**Why Postgres + Redis?** Postgres is the source of truth for the task log (auditable, queryable, backups). Redis is the work queue (fast, simple pub-sub). Together they separate coordination (store) from execution (agents) cleanly.

**Why Claude SDK + custom orchestrator?** Maximum control, no framework lock-in, aligned with Anthropic's recommended patterns. You can swap agents or models without framework overhead.

**Why Sonnet for workers, Opus for orchestrator/reviewer?** Orchestrator needs strong reasoning to decompose tasks and route correctly. Reviewer needs judgment authority. Sonnet is fast and capable enough for focused, single-objective tasks (design, code).

---

*Next update: after Phase 0 scaffolding + first demo run.*
