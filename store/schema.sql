-- Phase 0 schema: the state store that makes agents stateless.
-- Source of truth for runs, tasks, and the task graph. Idempotent: safe to
-- apply repeatedly (CLAUDE.md — restarted agents must be safe).

-- A run = one end-to-end workflow execution (one GitHub issue -> PR).
-- "workflow_id" on the wire (see schemas/message.py) == runs.run_id here.
CREATE TABLE IF NOT EXISTS runs (
    run_id        UUID PRIMARY KEY,
    issue_id      BIGINT      NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','success','failed')),
    created_by    TEXT        NOT NULL,           -- the human owner
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    final_pr_url  TEXT,
    total_cost_tokens BIGINT  NOT NULL DEFAULT 0,
    -- repo + feature spec: {owner, repo, base_branch, issue_number,
    -- title, body, auto_merge}. Drives the GitHub work at the end of a run.
    spec          JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- Forward-compat for databases created before `spec` existed.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS spec JSONB NOT NULL DEFAULT '{}'::jsonb;

-- One unit of work for one agent. Inputs/expected_output are the typed
-- contract; output is filled in when the task completes.
CREATE TABLE IF NOT EXISTS tasks (
    task_id         UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent           TEXT NOT NULL                 -- recipient role that executes
                    CHECK (agent IN ('orchestrator','designer','engineer','reviewer')),
    sender          TEXT NOT NULL,                -- who created the task
    status          TEXT NOT NULL DEFAULT 'waiting'
                    CHECK (status IN ('waiting','in_progress','done','failed')),
    inputs          JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_output JSONB NOT NULL DEFAULT '{}'::jsonb,
    output          JSONB,
    error           TEXT,
    max_steps       INT  NOT NULL DEFAULT 1,
    timeout_ms      INT  NOT NULL DEFAULT 300000,
    attempt_count   INT  NOT NULL DEFAULT 0,
    cost_tokens     BIGINT NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tasks_run    ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(agent, status);

-- Structure + ordering only. Task STATUS is authoritative on tasks; we keep
-- it out of here on purpose so it can never drift between two tables.
CREATE TABLE IF NOT EXISTS task_graph (
    run_id         UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    task_id        UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    parent_task_id UUID REFERENCES tasks(task_id) ON DELETE CASCADE,
    seq            INT  NOT NULL DEFAULT 0,        -- order within the run
    PRIMARY KEY (run_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_graph_parent ON task_graph(parent_task_id);
