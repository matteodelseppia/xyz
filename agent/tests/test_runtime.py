from __future__ import annotations

import asyncio
import sys

import pytest
from xyz_agent.runtime import RPC_LINE_LIMIT, PiSession


@pytest.mark.asyncio
async def test_pi_rpc_accepts_bounded_large_jsonl_event() -> None:
    evidence = [
        {
            "id": f"ev_{index:016x}",
            "source_id": "source",
            "url": "https://example.com/article",
            "title": "Article",
            "published_at": None,
            "text": "bounded evidence " * 1_000,
            "kind": "entry",
        }
        for index in range(1, 5)
    ]
    script = """
import json
import sys
for raw in sys.stdin:
    command = json.loads(raw)
    if command.get("type") == "prompt":
        print(json.dumps({"id": command["id"], "type": "response", "success": True}), flush=True)
        event = {
            "type": "tool_execution_end",
            "toolName": "read_entry",
            "result": {"details": {"evidence": EVIDENCE}},
        }
        print(json.dumps(event), flush=True)
        print(json.dumps({"type": "agent_settled"}), flush=True)
    elif command.get("type") == "get_session_stats":
        event = {
            "id": command["id"],
            "type": "response",
            "success": True,
            "data": {},
        }
        print(json.dumps(event), flush=True)
        break
"""
    script = script.replace("EVIDENCE", repr(evidence))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=RPC_LINE_LIMIT,
    )
    session = PiSession(process, "test", 5)
    try:
        result = await session.prompt("test")
        assert len(result.evidence) == 4
    finally:
        await session.close()
