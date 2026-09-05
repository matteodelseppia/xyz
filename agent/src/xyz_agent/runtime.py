from __future__ import annotations

import asyncio
import json
import os
import re
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .models import Evidence, Publication, Review

RPC_LINE_LIMIT = 4 * 1024 * 1024
STDERR_TAIL_LIMIT = 8 * 1024
SECRET_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]+|Bearer\s+\S+|OPENROUTER_API_KEY\s*[=:]\s*\S+)")


class RuntimeFailure(RuntimeError):
    pass


@dataclass
class SessionResult:
    terminal: Publication | Review | None
    evidence: list[Evidence] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    interactions: list[dict[str, Any]] = field(default_factory=list)


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
        self.stderr_tail = bytearray()
        self.stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        if self.process.stderr is None:
            return
        while chunk := await self.process.stderr.read(4096):
            self.stderr_tail.extend(chunk)
            if len(self.stderr_tail) > STDERR_TAIL_LIMIT:
                del self.stderr_tail[:-STDERR_TAIL_LIMIT]

    async def _stderr_summary(self) -> str:
        with suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(self.stderr_task), timeout=0.25)
        summary = bytes(self.stderr_tail).decode("utf-8", errors="replace").strip()
        summary = SECRET_RE.sub("[REDACTED]", summary)
        return summary[-2000:]

    async def _send(self, command: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.returncode is not None:
            raise RuntimeFailure(f"Pi {self.role} process is unavailable")
        self.process.stdin.write(json.dumps(command, separators=(",", ":")).encode() + b"\n")
        await self.process.stdin.drain()

    async def prompt(self, message: str) -> SessionResult:
        self.request += 1
        request_id = f"{self.role}-{self.request}"
        await self._send({"id": request_id, "type": "prompt", "message": message})
        result = SessionResult(
            terminal=None,
            interactions=[{"kind": "pi_input", "message": message}],
        )

        async def collect() -> SessionResult:
            if self.process.stdout is None:
                raise RuntimeFailure("Pi stdout is unavailable")
            accepted = False
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    detail = await self._stderr_summary()
                    suffix = f": {detail}" if detail else ""
                    raise RuntimeFailure(f"Pi {self.role} exited before settlement{suffix}")
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
                    # Tool response bodies are fetched evidence and must never leave the agent.
                    result.interactions.append(
                        {"kind": "tool", "name": name, "status": "completed"}
                    )
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
                            result.interactions.append(
                                {
                                    "kind": "pi_output",
                                    "artifact": result.terminal.model_dump(mode="json"),
                                }
                            )
                        except ValidationError as exc:
                            result.diagnostics.append(
                                f"invalid publication: {exc.error_count()} errors"
                            )
                    elif name == "submit_review" and details.get("review"):
                        try:
                            result.terminal = Review.model_validate(details["review"])
                            result.interactions.append(
                                {
                                    "kind": "pi_output",
                                    "review": result.terminal.model_dump(mode="json"),
                                }
                            )
                        except ValidationError as exc:
                            result.diagnostics.append(f"invalid review: {exc.error_count()} errors")
                elif event.get("type") == "extension_error":
                    result.diagnostics.append("source extension error")
                elif event.get("type") == "agent_end":
                    result.diagnostics.append("Pi agent ended with an error")
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
        thinking = getattr(self.settings, f"{role}_thinking")
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
            "--thinking",
            thinking,
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
            # Pi's JSONL tool events can contain bounded evidence payloads;
            # the default asyncio 64 KiB line limit is too small for them.
            limit=RPC_LINE_LIMIT,
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
