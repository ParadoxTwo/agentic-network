"""Code execution for the Engineer agent.

The Engineer proposes file contents; an Executor materializes them over a copy
of the repo and runs the test command, returning pass/fail. Two backends:

- LocalExecutor: runs in a temp dir via subprocess. Convenient for dev and the
  daemonless sandbox, but it runs model-generated code on the host with only a
  timeout for protection. NOT for untrusted production use.
- DockerExecutor: the production path (one ephemeral, network-less container
  per task; see docs/deployment.md). Stubbed here until the runtime lands.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel


class ExecutionResult(BaseModel):
    passed: bool
    returncode: int
    output: str


class LocalExecutor:
    """Run the test command in a throwaway copy of the repo. Dev/sandbox only."""

    def __init__(self, timeout_s: int = 120) -> None:
        self._timeout = timeout_s

    def run(
        self,
        files: dict[str, str],
        command: list[str],
        repo_dir: str | None = None,
    ) -> ExecutionResult:
        with tempfile.TemporaryDirectory(prefix="agentexec-") as tmp:
            workdir = Path(tmp)
            if repo_dir:
                shutil.copytree(repo_dir, workdir, dirs_exist_ok=True)
            for rel, content in files.items():
                target = workdir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            try:
                proc = subprocess.run(
                    command,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired:
                return ExecutionResult(
                    passed=False, returncode=124, output="timed out"
                )
            return ExecutionResult(
                passed=proc.returncode == 0,
                returncode=proc.returncode,
                output=(proc.stdout + proc.stderr)[-8000:],
            )


class DockerExecutor:  # pragma: no cover - production runtime, not built yet
    """Ephemeral, network-less container per task. Phase 5 runtime work."""

    def run(
        self,
        files: dict[str, str],
        command: list[str],
        repo_dir: str | None = None,
    ) -> ExecutionResult:
        raise NotImplementedError(
            "DockerExecutor is the production path (see docs/deployment.md); "
            "use LocalExecutor for dev until the sandbox runtime is built."
        )
