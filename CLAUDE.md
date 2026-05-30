# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Agentic network: a system of specialized AI agents that collaborate to execute real workflows (software delivery first), coordinated under a single human owner. The "org chart" (CEO, leads, senior/junior engineers, etc.) is a MENTAL MODEL for naming specialists and reasoning about responsibility — NOT a literal build target. Implement only roles and layers that do real, distinct work.

## Prime Directives

- **Build the simplest thing that works.** Add a layer, role, or framework only when a concrete need is demonstrated — never preemptively.
- **Be direct.** If a design is weak, over-engineered, or has a better path, say so plainly before building. Correction over agreement.
- **Distinguish well-grounded from speculative.** Flag assumptions; never present a guess as settled.
- **Keep the system runnable at every step.** A working end-to-end slice beats a broad scaffold.

## Architecture

### Core Pattern
- Shallow orchestrator-worker model: one coordinating agent per workflow decomposes tasks, delegates to specialists, then verifies and synthesizes results.
- No deep delegation chains. Every layer must do distinct, measurable work (task decomposition, specialized execution, review, integration) or be removed.
- Agents are tool-scoped and minimal: focused system prompt + smallest toolset needed + explicit objective, typed input/output contracts, and clear task boundaries.
- "Senior" vs "junior" must map to a CONCRETE difference (stronger model, broader tools/permissions, review authority, larger context) or the distinction is dropped.

### State & Coordination
- Agents are stateless. All task state, working memory, and history live in an external store.
- Messages between agents are typed (JSON schema) with: task_id, sender, recipient, inputs, expected_output, and termination condition.
- No open-ended dialogue between agents. No agent may invent new recipients or tasks off-schema.
- Coordinate horizontal work through the shared store, not free agent-to-agent conversation.

### Verification (First-Class)
- Every productive output passes a gate before acceptance: tests, linters, schema validation, or a reviewer agent with an explicit rubric and authority to reject.
- Failed gates route the task back with the failure reason — they do not silently pass.

### Containers & Runtime
- Use containers primarily to SANDBOX code/tool execution (one sandbox per code-running task).
- Prefer ephemeral workers that spin up per task and exit. Use a persistent service only for genuinely event-driven roles.
- Start with Docker Compose. Do not introduce Kubernetes or service meshes without demonstrated scaling need.

### Safety Rails
- Enforce hard caps per task: recursion depth, agent hops, total token spend, wall-clock timeout.
- The human owner can pause, inspect, and kill any run. Build this control plane early.
- Never hardcode secrets; read from environment or a secrets manager.

### Observability
- Trace every task across agents: who did what, inputs/outputs, tokens, latency, cost.
- If a behavior can't be traced, it isn't done.

## Intended Repo Layout

```
orchestrator/    Coordination + task decomposition logic
agents/          Specialist workers (one module per role)
schemas/         Message + task JSON schemas and validation
store/           State store + queue access layer
runtime/         Containers/compose, sandboxing setup
eval/            Test workflows + execution traces
```

## Checklist: Adding or Changing an Agent

Before building any new agent, fill in all six items:

1. **Single objective** in one sentence.
2. **Input and output** as typed JSON schemas.
3. **Exact tools** it may call; grant nothing more.
4. **Context:** what it reads, what it must NOT see.
5. **Termination condition** and max steps.
6. **Verification:** how its output is verified (tests, linter, reviewer agent with rubric).

If you can't fill all six, the agent isn't specified — ask the human owner before building it.

## Commands

<!-- Fill in as the project scaffolds. -->

- **Install dependencies:** [e.g., `uv sync`, `pip install -e .`]
- **Run (dev):** [e.g., `docker compose up`]
- **Run tests:** [e.g., `pytest -q`]
- **Run one test:** [e.g., `pytest tests/test_orchestrator.py -v`]
- **Lint / format:** [e.g., `ruff check . && ruff format .`]
- **Typecheck:** [e.g., `mypy .`]
- **Run one workflow locally:** [command TBD]

## Code Style

- Follow the linter and formatter; don't hand-enforce what they cover.
- Prefer small, pure, well-named functions and explicit types at boundaries.
- Write a test with each new behavior.

## Current Focus

**First milestone:** ONE real workflow end-to-end (e.g., feature request → design → implement → review → tests pass → PR) with:
- One orchestrator
- 2–3 specialists
- A reviewer gate
- External state store
- Execution tracing

No CEO / marketing / HR layers yet. Earn each addition.

## Do NOT

- Build the full corporate hierarchy as nested delegating agents.
- Spin up a persistent server per role by default.
- Let agents free-chat with each other.
- Add a role, layer, or framework without a concrete, stated job it uniquely does.
