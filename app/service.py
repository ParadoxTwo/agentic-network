"""The long-running service: orchestrator loop + worker pool.

Wires real agents (Anthropic) and a real GitHub client around the store. Each
worker runs in its own thread with its OWN database connection, because a
psycopg connection is not safe to share across threads. The orchestrator ticks
in the main thread. All state is in Postgres, so the process is restart-safe.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from agents.base import Agent
from agents.designer import Designer
from agents.engineer import Engineer
from agents.llm import LLM, AnthropicLLM
from agents.reviewer import Reviewer
from app.config import Config
from github.client import GitHubClient
from orchestrator.orchestrator import Orchestrator
from orchestrator.worker import Worker
from schemas.message import AgentRole
from store.db import apply_schema, connect_pg, connect_redis
from store.queue import TaskQueue
from store.tasklog import TaskLog

log = logging.getLogger("service")

LLMFactory = Callable[[AgentRole], LLM]


def build_agents(llm_for: LLMFactory) -> dict[AgentRole, Agent]:
    """Construct the three specialists. `llm_for` lets tests inject fakes."""
    return {
        AgentRole.DESIGNER: Designer(llm_for(AgentRole.DESIGNER)),
        AgentRole.ENGINEER: Engineer(llm_for(AgentRole.ENGINEER)),
        AgentRole.REVIEWER: Reviewer(llm_for(AgentRole.REVIEWER)),
    }


class Service:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._stop = threading.Event()
        self._workers: list[Worker] = []

    def _llm_for(self, role: AgentRole) -> LLM:
        model = {
            AgentRole.DESIGNER: self._config.designer_model,
            AgentRole.ENGINEER: self._config.engineer_model,
            AgentRole.REVIEWER: self._config.reviewer_model,
        }[role]
        return AnthropicLLM(model)

    def _worker_loop(self, role: AgentRole, agent: Agent) -> None:
        # Own connection per worker thread (psycopg is not thread-safe).
        worker = Worker(role, agent, TaskLog(connect_pg()), TaskQueue(connect_redis()))
        self._workers.append(worker)
        while not self._stop.is_set():
            worker.run_once(timeout=5)

    def run(self) -> None:
        conn = connect_pg()
        apply_schema(conn)
        orchestrator = Orchestrator(
            TaskLog(conn),
            TaskQueue(connect_redis()),
            GitHubClient(self._config.github_token),
        )
        agents = build_agents(self._llm_for)
        for role, agent in agents.items():
            threading.Thread(
                target=self._worker_loop, args=(role, agent), daemon=True
            ).start()

        log.info("service up: %d workers + orchestrator", len(agents))
        while not self._stop.is_set():
            orchestrator.tick()
            time.sleep(self._config.poll_interval_s)

    def stop(self) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.stop()


def main() -> None:  # pragma: no cover - entrypoint
    logging.basicConfig(level=logging.INFO)
    Service(Config.from_env()).run()


if __name__ == "__main__":  # pragma: no cover
    main()
