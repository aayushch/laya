# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Installed-CLI-agent inference backend — a parallel to the LiteLLM path.

Some users have an enterprise/subscription coding agent installed (Claude Code on
a Teams plan, ChatGPT/Codex, Gemini CLI, Pi) but no model API key and no spare VRAM
for local models. This backend drives those agents *as the LLM* — non-interactively,
with structured output — so Laya's pipeline runs on the agent's own quota instead of
an API key or local VRAM.

It is invoked from ``llm_call`` (``llm/client.py``) when the resolved model string is
``agent/<agent_id>/<model_string>``. It returns an :class:`AgentResult`; client.py
maps it into the same ``LLMResponse`` and keeps the shared audit-log + budget side
effects, so the contract is identical to the LiteLLM path.

Capability tiers (validated against the real CLIs):
  - **native** (Claude Code): ``--json-schema`` forces schema-valid output, returned
    pre-parsed in the ``structured_output`` field — same tool-based enforcement the
    Anthropic API uses.
  - **best_effort** (Codex / Gemini / Pi): no schema flag, so we inject the schema as
    a text instruction (the same one client.py uses for local providers), parse the
    JSON out of the reply, validate it, and retry on failure.

Out of scope (handled on the LiteLLM/local path): streaming chat + Coherence, which
need a multi-round tool loop the agents can't run for us.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any

import structlog

from laya.agents.subprocess_helper import AgentProcess
from laya.config import detect_agent_paths, get_agent_binary

log = structlog.get_logger()

# Per-agent structured-output capability. "native" agents enforce the schema for us;
# "best_effort" agents get the schema as a prompt instruction + validate/retry.
AGENT_TIERS: dict[str, str] = {
    "claude_code": "native",
    "codex_cli": "best_effort",
    "gemini_cli": "best_effort",
    "pi_cli": "best_effort",
}

# Built-in tools we deny so an inference call can never touch the filesystem, run a
# command, or hit the network — we want a pure completion, not an agent session. Each
# agent also runs in an ephemeral empty cwd (see _run_agent) so it has nothing to act on.
_CLAUDE_BUILTIN_TOOLS = (
    "Bash Edit Write Read WebFetch WebSearch Glob Grep Task TodoWrite NotebookEdit"
)

# Same schema-as-text instruction client.py appends for providers without native
# structured output — kept verbatim so best-effort agents see the identical contract.
_SCHEMA_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON matching this exact schema. "
    "Output ONLY the JSON object, no other text.\n\nSchema:\n{schema_text}"
)

# Transport-layer harness neutralizer. CLI agents ship a coding-assistant system prompt
# that, on a terse free-text task, makes the model ask clarifying questions or offer to
# take actions instead of just answering (schema calls are immune — the forced JSON tool
# can't be a question; this only bites no-schema stages like the briefing). No CLI flag
# disables that, so we prepend this directive ahead of the stage's own system prompt. It
# is NOT a stage prompt and never edits one — the stage prompt (single source of truth,
# shared with the LiteLLM path) follows it unchanged. Its only effect is to bring the
# agent's behavior in line with the API path (answer directly), so output stays faithful.
_NONINTERACTIVE_DIRECTIVE = (
    "You are operating as a non-interactive inference engine for automated text "
    "processing. Produce the requested output directly and completely in this single "
    "response. Do not ask clarifying questions, request more context, propose next "
    "steps, or offer to perform actions — return only the requested output."
)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)

_semaphore: asyncio.Semaphore | None = None


@dataclass
class AgentResult:
    """What an agent inference call produced — mapped to LLMResponse by client.py."""

    content: str
    parsed: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"
    truncated: bool = False
    effective_model: str = ""
    cost_usd: float | None = None
    # Native usage-limit signal (Claude Code's rate_limit_info), for agent budgeting.
    rate_limit_info: dict | None = None


# ── helpers ──────────────────────────────────────────────────────────────


def is_agent_model(model: str | None) -> bool:
    """True if a resolved model string targets the agent backend."""
    return bool(model) and model.startswith("agent/")


def parse_agent_model_id(model_id: str) -> tuple[str, str | None]:
    """Split ``agent/<agent_id>/<model_string>`` → (agent_id, model_string|None).

    Splits on the first two slashes only: the agent id is a known enum (never contains
    a slash), and the remainder is the user-typed model string verbatim — so slugs with
    their own slashes (e.g. ``lmstudio/qwen3.6-35b-a3b``) survive intact. A missing third
    segment means "use the agent's own default model" (no --model flag).
    """
    parts = model_id.split("/", 2)
    if len(parts) < 2 or parts[0] != "agent":
        raise ValueError(f"Not an agent model id: {model_id!r}")
    agent_id = parts[1]
    model_string = parts[2] if len(parts) == 3 and parts[2].strip() else None
    return agent_id, model_string


def capabilities() -> list[dict[str, Any]]:
    """Per-agent availability + tier for the Settings UI agent picker."""
    detected = detect_agent_paths()
    out: list[dict[str, Any]] = []
    for agent_id, tier in AGENT_TIERS.items():
        path = detected.get(agent_id, "")
        out.append({
            "agent_id": agent_id,
            "available": bool(path),
            "path": path,
            "tier": tier,
        })
    return out


def _get_semaphore() -> asyncio.Semaphore:
    """Bound concurrent agent processes — each spawns a process and runs 5–12s."""
    global _semaphore
    if _semaphore is None:
        from laya.config import load_settings

        n = int(load_settings().get("agent_backend_concurrency", 3) or 3)
        _semaphore = asyncio.Semaphore(max(1, n))
    return _semaphore


def _strip_think_blocks(text: str) -> str:
    if "<think>" not in text:
        return text
    result = _THINK_BLOCK_RE.sub("", text).strip()
    if result.startswith("<think>"):
        result = result[len("<think>"):].strip()
    return result


def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Collapse OpenAI-format messages into (system_text, user_prompt).

    Pipeline stages send a system + a single user message; we also tolerate multi-turn
    by labelling roles. Content may be a string or a content-block list (post-caching);
    we flatten either way.
    """
    def _text(content: Any) -> str:
        if isinstance(content, list):
            return "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        return str(content or "")

    system_parts: list[str] = []
    turns: list[tuple[str, str]] = []
    for m in messages:
        role = m.get("role")
        text = _text(m.get("content"))
        if role == "system":
            system_parts.append(text)
        else:
            turns.append((role or "user", text))

    system_text = "\n\n".join(p for p in system_parts if p)
    if len(turns) == 1:
        user_prompt = turns[0][1]
    else:
        user_prompt = "\n\n".join(f"[{r}]\n{t}" for r, t in turns)
    return system_text, user_prompt


def _extract_json(text: str) -> dict | None:
    """Pull the first complete JSON object out of an agent reply (best-effort path).

    Mirrors client.py: strip <think> blocks and markdown fences, then parse. Uses
    raw_decode from the first ``{`` so trailing prose doesn't break parsing.
    """
    if not text:
        return None
    stripped = _strip_think_blocks(text).strip()
    if stripped.startswith("```"):
        nl = stripped.find("\n")
        if nl != -1:
            stripped = stripped[nl + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    start = stripped.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stripped[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_against_schema(parsed: dict, response_schema: dict) -> tuple[bool, str]:
    """Validate parsed JSON against the (inner) JSON schema.

    Uses jsonschema when importable (it ships transitively via litellm); otherwise
    falls back to a top-level required-keys check, which catches the common failure
    modes (truncation, missing fields). Returns (ok, error_message).
    """
    schema = response_schema.get("schema", response_schema)
    try:
        import jsonschema

        jsonschema.validate(instance=parsed, schema=schema)
        return True, ""
    except ImportError:
        required = schema.get("required", []) if isinstance(schema, dict) else []
        missing = [k for k in required if k not in parsed]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"
        return True, ""
    except Exception as e:  # jsonschema.ValidationError
        return False, str(e).splitlines()[0] if str(e) else "schema validation failed"


# ── per-agent process runner ─────────────────────────────────────────────


def _build_args(
    agent_id: str,
    binary: str,
    system_text: str,
    user_prompt: str,
    model_string: str | None,
    response_schema: dict | None,
    native: bool,
) -> tuple[list[str], dict[str, str]]:
    """Build the argv + env for one agent invocation. env overrides only.

    NON-INTERACTIVE GUARANTEE (text processing must never block on a human): every agent
    runs in one-shot headless mode with stdin closed (AgentProcess spawns with
    stdin=DEVNULL), so a clarifying question can only ever be emitted as output text — it
    can never pause the turn waiting for a reply. On top of that we disable tools and any
    "plan/act"/approval gating per-agent below, so there's nothing to approve and no
    workspace-trust prompt to satisfy. None of this touches the stage prompts (single
    source of truth shared with the LiteLLM path) — it's all calling-convention.
    """
    env: dict[str, str] = {}

    if agent_id == "claude_code":
        # -p = one-shot print mode (exits, never waits). All built-in tools denied = pure
        # inference, nothing to request approval for. NO --mcp-config (keeps the per-call
        # system-prompt tax down and avoids loading Laya's tools).
        #
        # permission-mode=default (NOT plan): in plan mode Claude wraps the answer in
        # "I'm in plan mode … once plan mode is exited I can help you …" preamble — a
        # plan-and-wait framing we must avoid. In headless -p with tools denied, default
        # mode cannot prompt (no TTY) and cannot act, so it just answers directly.
        #
        # stream-json (+ --verbose, required) instead of plain json so we also capture the
        # rate_limit_event (window reset + status) for agent usage-budgeting; the final
        # `result` event still carries structured_output + usage + total_cost_usd.
        args = [binary, "-p", user_prompt, "--output-format", "stream-json", "--verbose",
                "--permission-mode", "default", "--disallowedTools", _CLAUDE_BUILTIN_TOOLS]
        if system_text:
            args += ["--append-system-prompt", system_text]
        if model_string:
            args += ["--model", model_string]
        if native and response_schema:
            schema = response_schema.get("schema", response_schema)
            args += ["--json-schema", json.dumps(schema)]
        return args, env

    if agent_id == "pi_cli":
        # -p = one-shot non-interactive; --no-tools = nothing to approve; --no-session =
        # don't litter the cwd. Pi's plan-mode extension is not enabled, so no plan/wait.
        args = [binary, "-p", "--mode", "json", "--no-tools", "--no-session"]
        if system_text:
            args += ["--system-prompt", system_text]
        if model_string:
            args += ["--model", model_string]
        args.append(user_prompt)
        return args, env

    if agent_id == "gemini_cli":
        # -p = one-shot headless; --approval-mode plan = read-only (no edits, and headless
        # so nothing to approve). GEMINI_CLI_TRUST_WORKSPACE skips the folder-trust prompt
        # that otherwise aborts headless runs in an untrusted dir.
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        prompt = f"{system_text}\n\n{user_prompt}" if system_text else user_prompt
        args = [binary, "-p", prompt, "-o", "json", "--approval-mode", "plan"]
        if model_string:
            args += ["-m", model_string]
        return args, env

    if agent_id == "codex_cli":
        # exec = the non-interactive automation mode (runs to completion, never prompts);
        # --sandbox read-only = pure inference; --ask-for-approval never = explicitly never
        # pause for an approval decision.
        prompt = f"{system_text}\n\n{user_prompt}" if system_text else user_prompt
        args = [binary, "exec", "--json", "--sandbox", "read-only",
                "--ask-for-approval", "never"]
        if model_string:
            args += ["--model", model_string]
        args.append(prompt)
        return args, env

    raise ValueError(f"Unknown agent backend: {agent_id}")


def _parse_output(agent_id: str, lines: list[str], native_schema: bool) -> dict[str, Any]:
    """Parse an agent's stdout into a normalized dict.

    Returns: content, parsed (native only), input_tokens, output_tokens,
    finish_reason, effective_model, cost_usd, error.
    """
    out: dict[str, Any] = {
        "content": "", "parsed": None, "input_tokens": 0, "output_tokens": 0,
        "finish_reason": "stop", "effective_model": "", "cost_usd": None,
        "rate_limit_info": None, "error": None,
    }
    buf = "\n".join(lines)

    if agent_id == "claude_code":
        # stream-json: scan for the final `result` event (the answer + usage) and the
        # `rate_limit_event` (usage-window state). Other event types are ignored.
        data: dict = {}
        for line in lines:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "result":
                data = ev
            elif etype == "rate_limit_event":
                out["rate_limit_info"] = ev.get("rate_limit_info")
        if data.get("is_error"):
            out["error"] = str(data.get("result") or data.get("error") or "claude error")
            return out
        if native_schema:
            out["parsed"] = data.get("structured_output")
        out["content"] = data.get("result") or (
            json.dumps(out["parsed"]) if out["parsed"] is not None else ""
        )
        usage = data.get("usage") or {}
        out["input_tokens"] = (
            int(usage.get("input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0))
        )
        out["output_tokens"] = int(usage.get("output_tokens", 0))
        out["cost_usd"] = data.get("total_cost_usd")
        model_usage = data.get("modelUsage") or {}
        out["effective_model"] = next(iter(model_usage), "") or ""
        if data.get("stop_reason") == "max_tokens":
            out["finish_reason"] = "length"
        return out

    if agent_id == "gemini_cli":
        data = _extract_json(buf) or {}
        if data.get("error"):
            err = data["error"]
            out["error"] = err.get("message") if isinstance(err, dict) else str(err)
            return out
        out["content"] = str(data.get("response", ""))
        stats = data.get("stats") or {}
        out["input_tokens"], out["output_tokens"] = _gemini_tokens(stats)
        return out

    # JSONL event streams: Pi and Codex
    if agent_id == "pi_cli":
        return _parse_pi(lines, out)
    if agent_id == "codex_cli":
        return _parse_codex(lines, out)

    return out


def _gemini_tokens(stats: dict) -> tuple[int, int]:
    """Best-effort token extraction from Gemini's nested stats blob."""
    def _find(d: Any, keys: tuple[str, ...]) -> int:
        if isinstance(d, dict):
            for k, v in d.items():
                if k in keys and isinstance(v, (int, float)):
                    return int(v)
                r = _find(v, keys)
                if r:
                    return r
        elif isinstance(d, list):
            for v in d:
                r = _find(v, keys)
                if r:
                    return r
        return 0
    return _find(stats, ("prompt", "promptTokenCount", "input")), _find(
        stats, ("candidates", "candidatesTokenCount", "output")
    )


def _parse_pi(lines: list[str], out: dict[str, Any]) -> dict[str, Any]:
    """Accumulate Pi's assistant text + usage from its JSON event stream."""
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        msg = ev.get("message") or {}
        if etype in ("message_update", "message_end") and msg.get("role") == "assistant":
            content = msg.get("content") or []
            text = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
            if text:
                out["content"] = text
            usage = msg.get("usage") or {}
            if usage.get("input") or usage.get("output"):
                out["input_tokens"] = int(usage.get("input", 0))
                out["output_tokens"] = int(usage.get("output", 0))
                cost = usage.get("cost") or {}
                out["cost_usd"] = cost.get("total") if isinstance(cost, dict) else None
            if msg.get("model"):
                out["effective_model"] = str(msg["model"])
    return out


def _parse_codex(lines: list[str], out: dict[str, Any]) -> dict[str, Any]:
    """Accumulate Codex's final agent_message + usage from its JSONL events."""
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type", "")
        if etype.startswith("item.") :
            item = ev.get("item") or {}
            if item.get("type") in ("agent_message", "assistant_message"):
                text = item.get("text") or item.get("message") or ""
                if text:
                    out["content"] = str(text)
        elif etype == "turn.completed":
            usage = ev.get("usage") or {}
            out["input_tokens"] = int(usage.get("input_tokens", usage.get("input", 0)))
            out["output_tokens"] = int(usage.get("output_tokens", usage.get("output", 0)))
        elif etype == "error":
            out["error"] = str(ev.get("message") or ev.get("error") or "codex error")
    return out


async def _run_agent(
    agent_id: str,
    binary: str,
    system_text: str,
    user_prompt: str,
    model_string: str | None,
    response_schema: dict | None,
    native: bool,
    timeout: float,
) -> dict[str, Any]:
    """Spawn the agent once in an ephemeral empty cwd and return parsed output."""
    args, env = _build_args(
        agent_id, binary, system_text, user_prompt, model_string, response_schema, native
    )
    # Ephemeral, empty working dir: the agent has no repo to read or write, reinforcing
    # the tool/sandbox flags above. Cleaned up regardless of outcome.
    tmpdir = tempfile.mkdtemp(prefix="laya-agent-llm-")
    proc = AgentProcess()
    try:
        await proc.spawn(args=args, cwd=tmpdir, env=env or None)

        async def _collect() -> list[str]:
            collected: list[str] = []
            async for line in proc.read_lines(idle_timeout=timeout):
                collected.append(line)
            await proc.wait()
            return collected

        try:
            lines = await asyncio.wait_for(_collect(), timeout=timeout)
        except asyncio.TimeoutError:
            await proc.terminate()
            raise TimeoutError(f"agent '{agent_id}' timed out after {timeout}s")
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Agent '{agent_id}' binary not found ({binary}). Is it installed?"
        ) from e
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    result = _parse_output(agent_id, lines, native_schema=native and bool(response_schema))
    if result.get("error"):
        stderr_tail = proc.stderr_output
        raise RuntimeError(
            f"Agent '{agent_id}' error: {result['error']}"
            + (f" | stderr: {stderr_tail[-500:]}" if stderr_tail else "")
        )
    return result


# ── public entry point ───────────────────────────────────────────────────


async def agent_llm_call(
    model_id: str,
    messages: list[dict[str, Any]],
    response_schema: dict | None = None,
    max_tokens: int = 65536,
    temperature: float = 0.0,
    num_retries: int = 3,
) -> AgentResult:
    """Run one inference through an installed CLI agent. Mirrors the llm_call contract.

    For native-schema agents (Claude Code) the schema is enforced by the CLI and the
    parsed object is returned directly. For best-effort agents the schema is injected as
    a prompt instruction, then the reply is parsed + validated + retried up to
    ``num_retries`` times before giving up (``parsed=None``, like the LiteLLM path).
    """
    agent_id, model_string = parse_agent_model_id(model_id)
    if agent_id not in AGENT_TIERS:
        raise ValueError(f"Unknown agent backend: {agent_id!r}")

    binary = get_agent_binary(agent_id)
    native = AGENT_TIERS[agent_id] == "native"
    system_text, user_prompt = _split_messages(messages)

    # Prepend the non-interactive directive ahead of the (unchanged) stage system prompt
    # so the agent answers directly instead of asking clarifying questions.
    base_system = (
        _NONINTERACTIVE_DIRECTIVE + ("\n\n" + system_text if system_text else "")
    ).strip()
    # Best-effort agents have no schema flag — give them the schema as text, the same
    # way client.py does for local providers without native structured output.
    if response_schema and not native:
        schema_text = json.dumps(response_schema.get("schema", response_schema), indent=2)
        base_system = (base_system + _SCHEMA_INSTRUCTION.format(schema_text=schema_text)).strip()

    from laya.pipeline.queue import get_model_timeout

    timeout = float(get_model_timeout())
    attempts = max(1, min(num_retries, 3)) if response_schema else 1
    last_content = ""
    last_usage = {"input_tokens": 0, "output_tokens": 0}
    last_extra: dict[str, Any] = {}
    retry_feedback = ""

    async with _get_semaphore():
        for attempt in range(attempts):
            system_for_attempt = base_system
            if retry_feedback:
                system_for_attempt = (base_system + retry_feedback).strip()

            start = time.monotonic()
            result = await _run_agent(
                agent_id, binary, system_for_attempt, user_prompt, model_string,
                response_schema, native, timeout,
            )
            log.info(
                "agent_llm_attempt",
                agent_id=agent_id, attempt=attempt + 1, native=native,
                latency_ms=int((time.monotonic() - start) * 1000),
                output_tokens=result.get("output_tokens", 0),
            )
            last_content = result["content"]
            last_usage = {
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
            }
            last_extra = {
                "finish_reason": result["finish_reason"],
                "effective_model": result["effective_model"] or (model_string or agent_id),
                "cost_usd": result["cost_usd"],
                "rate_limit_info": result.get("rate_limit_info"),
            }

            if not response_schema:
                parsed = None
                break

            # Native agents return the validated object directly.
            parsed = result["parsed"] if native else _extract_json(result["content"])
            if parsed is not None:
                ok, err = _validate_against_schema(parsed, response_schema)
                if ok:
                    break
                retry_feedback = (
                    f"\n\nYour previous response did not match the schema: {err}. "
                    f"Return ONLY a corrected JSON object matching the schema exactly."
                )
            else:
                retry_feedback = (
                    "\n\nYour previous response was not valid JSON. "
                    "Return ONLY a single JSON object matching the schema, no other text."
                )
            log.warning(
                "agent_llm_invalid_output",
                agent_id=agent_id, attempt=attempt + 1, will_retry=attempt + 1 < attempts,
            )

    return AgentResult(
        content=last_content,
        parsed=parsed if response_schema else None,
        input_tokens=last_usage["input_tokens"],
        output_tokens=last_usage["output_tokens"],
        finish_reason=last_extra.get("finish_reason", "stop"),
        truncated=last_extra.get("finish_reason") == "length",
        effective_model=last_extra.get("effective_model", ""),
        cost_usd=last_extra.get("cost_usd"),
        rate_limit_info=last_extra.get("rate_limit_info"),
    )
