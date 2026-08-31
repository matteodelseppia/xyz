from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .models import Evidence, Publication, Review


class RuntimeFailure(RuntimeError):
    pass


@dataclass
class SessionResult:
    terminal: Publication | Review | None
    evidence: list[Evidence] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class RoleSession(ABC):
    @abstractmethod
    async def prompt(self, message: str) -> SessionResult: ...

    @abstractmethod
    async def close(self) -> None: ...


class AgentRuntime(ABC):
    @abstractmethod
    async def start(self, role: str) -> RoleSession: ...

    async def close(self) -> None:
        return None


class PiSession(RoleSession):
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        role: str,
        timeout: float,
    ) -> None:
        self.process = process
        self.role = role
        self.timeout = timeout
        self.request = 0
        self.stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        if self.process.stderr is None:
            return
        while line := await self.process.stderr.readline():
            # Do not include provider payloads or source bodies in application logs.
            _ = line

    async def _send(self, command: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.returncode is not None:
            raise RuntimeFailure(f"Pi {self.role} process is unavailable")
        self.process.stdin.write(json.dumps(command, separators=(",", ":")).encode() + b"\n")
        await self.process.stdin.drain()

    async def prompt(self, message: str) -> SessionResult:
        self.request += 1
        request_id = f"{self.role}-{self.request}"
        await self._send({"id": request_id, "type": "prompt", "message": message})
        result = SessionResult(terminal=None)

        async def collect() -> SessionResult:
            if self.process.stdout is None:
                raise RuntimeFailure("Pi stdout is unavailable")
            accepted = False
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeFailure(f"Pi {self.role} exited before settlement")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeFailure("Pi emitted invalid JSONL") from exc
                if event.get("type") == "response" and event.get("id") == request_id:
                    if not event.get("success"):
                        raise RuntimeFailure(str(event.get("error", "prompt rejected")))
                    accepted = True
                elif event.get("type") == "tool_execution_end":
                    name = event.get("toolName")
                    details = event.get("result", {}).get("details", {})
                    evidence_value = details.get("evidence")
                    if evidence_value:
                        values = (
                            evidence_value if isinstance(evidence_value, list) else [evidence_value]
                        )
                        for value in values:
                            try:
                                evidence = Evidence.model_validate(value)
                            except ValidationError:
                                result.diagnostics.append(f"malformed evidence from {name}")
                                continue
                            if all(existing.id != evidence.id for existing in result.evidence):
                                result.evidence.append(evidence)
                    if name == "submit_publication" and details.get("artifact"):
                        try:
                            result.terminal = Publication.model_validate(details["artifact"])
                        except ValidationError as exc:
                            result.diagnostics.append(
                                f"invalid publication: {exc.error_count()} errors"
                            )
                    elif name == "submit_review" and details.get("review"):
                        try:
                            result.terminal = Review.model_validate(details["review"])
                        except ValidationError as exc:
                            result.diagnostics.append(f"invalid review: {exc.error_count()} errors")
                elif event.get("type") == "extension_error":
                    result.diagnostics.append("source extension error")
                elif event.get("type") == "agent_settled" and accepted:
                    await self._send({"id": f"stats-{request_id}", "type": "get_session_stats"})
                elif event.get("type") == "response" and event.get("id") == f"stats-{request_id}":
                    if event.get("success"):
                        result.usage = event.get("data", {})
                    return result

        try:
            return await asyncio.wait_for(collect(), timeout=self.timeout)
        except TimeoutError as exc:
            await self._send({"type": "abort"})
            raise RuntimeFailure(f"Pi {self.role} session timed out") from exc

    async def close(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.stderr_task.cancel()


class PiRuntime(AgentRuntime):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: list[PiSession] = []

    async def start(self, role: str) -> RoleSession:
        if role not in ("producer", "reviewer"):
            raise ValueError(role)
        provider = getattr(self.settings, f"{role}_provider")
        model = getattr(self.settings, f"{role}_model")
        prompt = (self.settings.prompt_dir / f"{role}.md").read_text()
        shared = (self.settings.prompt_dir / "shared-guidelines.md").read_text()
        command = [
            "pi",
            "--mode",
            "rpc",
            "--no-session",
            "--provider",
            provider,
            "--model",
            model,
            "--system-prompt",
            f"{prompt}\n\n{shared}",
            "--no-builtin-tools",
            "--no-extensions",
            "--extension",
            str(self.settings.pi_extension_path),
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
        ]
        environment = os.environ | self.settings.extension_environment(role)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=Path.cwd(),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        session = PiSession(process, role, self.settings.session_timeout_seconds)
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        await asyncio.gather(
            *(session.close() for session in self.sessions), return_exceptions=True
        )


class ScriptedSession(RoleSession):
    def __init__(self, responses: list[SessionResult]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    async def prompt(self, message: str) -> SessionResult:
        self.prompts.append(message)
        if not self.responses:
            raise RuntimeFailure("fake session has no remaining response")
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


class FakeRuntime(AgentRuntime):
    def __init__(self, producer: list[SessionResult], reviewer: list[SessionResult]) -> None:
        self.producer = ScriptedSession(producer)
        self.reviewer = ScriptedSession(reviewer)

    async def start(self, role: str) -> RoleSession:
        return self.producer if role == "producer" else self.reviewer
