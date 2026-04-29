"""Optional full prompt/output auditing for pressure tests.

The audit layer is intentionally environment-gated so normal app usage does not
write prompts. When enabled, it stores complete prompt/output payloads under an
artifact directory, grouped by arm and chapter.
"""
from __future__ import annotations

import contextlib
import contextvars
import argparse
from collections import Counter, defaultdict
import hashlib
import inspect
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable


_AUDIT_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("llm_audit_context", default={})
_ACTIVE_AUDIT_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar("llm_audit_active_call", default=False)

AUDIT_PHASES = {
    "chapter_generation_stream",
    "chapter_generation_beat",
    "chapter_outline_suggestion",
    "chapter_narrative_sync",
    "chapter_review_character",
    "chapter_review_timeline",
    "chapter_review_storyline",
    "chapter_review_foreshadow",
    "evolution_agent_control_card",
    "evolution_agent_reflection",
    "scoring",
    "unknown",
}


@contextlib.contextmanager
def llm_audit_context(**metadata: Any):
    """Temporarily attach audit metadata to downstream LLM calls."""

    current = dict(_AUDIT_CONTEXT.get() or {})
    clean = {key: value for key, value in metadata.items() if value is not None}
    token = _AUDIT_CONTEXT.set({**current, **clean})
    try:
        yield
    finally:
        _AUDIT_CONTEXT.reset(token)


def audit_enabled() -> bool:
    return str(os.getenv("LLM_AUDIT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


async def audit_generate_call(
    call: Callable[[], Awaitable[Any]],
    *,
    prompt: Any,
    config: Any,
    metadata: dict[str, Any] | None = None,
) -> Any:
    if not audit_enabled() or _ACTIVE_AUDIT_CALL.get():
        return await call()

    record = _start_record(prompt=prompt, config=config, metadata=metadata, stream=False)
    token = _ACTIVE_AUDIT_CALL.set(True)
    started = time.perf_counter()
    try:
        result = await call()
        duration = time.perf_counter() - started
        content = str(getattr(result, "content", result) or "")
        _finish_record(record, output=content, token_usage=_token_usage_to_dict(getattr(result, "token_usage", None)), duration=duration)
        return result
    except Exception as exc:
        duration = time.perf_counter() - started
        _finish_record(record, output="", token_usage={}, duration=duration, status="error", error=str(exc))
        raise
    finally:
        _ACTIVE_AUDIT_CALL.reset(token)


async def audit_stream_call(
    stream: Callable[[], AsyncIterator[str]],
    *,
    prompt: Any,
    config: Any,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    if not audit_enabled() or _ACTIVE_AUDIT_CALL.get():
        async for chunk in stream():
            yield chunk
        return

    record = _start_record(prompt=prompt, config=config, metadata=metadata, stream=True)
    token = _ACTIVE_AUDIT_CALL.set(True)
    started = time.perf_counter()
    chunks: list[str] = []
    try:
        async for chunk in stream():
            text = str(chunk or "")
            chunks.append(text)
            _append_chunk(record, text)
            yield chunk
        duration = time.perf_counter() - started
        _finish_record(record, output="".join(chunks), token_usage=_estimated_stream_usage(prompt, chunks), duration=duration)
    except Exception as exc:
        duration = time.perf_counter() - started
        _finish_record(record, output="".join(chunks), token_usage=_estimated_stream_usage(prompt, chunks), duration=duration, status="error", error=str(exc))
        raise
    finally:
        _ACTIVE_AUDIT_CALL.reset(token)


def _start_record(*, prompt: Any, config: Any, metadata: dict[str, Any] | None, stream: bool) -> dict[str, Any]:
    ctx = {**(_AUDIT_CONTEXT.get() or {}), **(metadata or {})}
    phase = _normalize_phase(str(ctx.get("phase") or _infer_phase()))
    novel_id = str(ctx.get("novel_id") or "")
    chapter_number = _int_or_none(ctx.get("chapter_number"))
    arm = str(ctx.get("arm") or _infer_arm(novel_id) or "unknown")
    call_id = f"{phase}_{chapter_number or 'na'}_{uuid.uuid4().hex[:10]}"
    prompt_payload = _prompt_to_dict(prompt)
    config_payload = _config_to_dict(config)
    output_dir = _call_output_dir(arm=arm, chapter_number=chapter_number, phase=phase, call_id=call_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_hash = _hash_json(prompt_payload)
    base = {
        "schema_version": 1,
        "call_id": call_id,
        "run_id": os.getenv("LLM_AUDIT_RUN_ID") or "",
        "arm": arm,
        "novel_id": novel_id,
        "chapter_number": chapter_number,
        "phase": phase,
        "source": str(ctx.get("source") or _infer_source()),
        "stream": stream,
        "model": str(getattr(config, "model", "") or ""),
        "config": config_payload,
        "prompt_hash": prompt_hash,
        "prompt_chars": _payload_chars(prompt_payload),
        "paths": {
            "dir": str(output_dir),
            "prompt": str(output_dir / "prompt.json"),
            "chunks": str(output_dir / "chunks.jsonl") if stream else "",
            "output": str(output_dir / "output.md"),
            "usage": str(output_dir / "usage.json"),
        },
        "status": "started",
        "started_at": _iso_now(),
        "metadata": _redact_sensitive(
            _json_safe({key: value for key, value in ctx.items() if key not in {"api_key", "headers", "cookies"}})
        ),
    }
    _write_json(output_dir / "prompt.json", {"prompt": prompt_payload, "config": config_payload, "record": base})
    if stream:
        (output_dir / "chunks.jsonl").write_text("", encoding="utf-8")
    return {**base, "_dir": output_dir, "_prompt_payload": prompt_payload}


def _finish_record(
    record: dict[str, Any],
    *,
    output: str,
    token_usage: dict[str, Any],
    duration: float,
    status: str = "success",
    error: str = "",
) -> None:
    output_dir = Path(record["_dir"])
    output_text = _redact_text(str(output or ""))
    output_hash = _hash_text(output_text)
    usage = {
        "token_usage": _json_safe(token_usage),
        "duration_seconds": round(duration, 3),
        "status": status,
        "error": error,
    }
    (output_dir / "output.md").write_text(output_text, encoding="utf-8")
    _write_json(output_dir / "usage.json", usage)
    public = {key: value for key, value in record.items() if not key.startswith("_")}
    public.update(
        {
            "status": status,
            "error": error,
            "finished_at": _iso_now(),
            "duration_seconds": round(duration, 3),
            "output_hash": output_hash,
            "output_chars": len(output_text),
            "token_usage": _json_safe(token_usage),
        }
    )
    _append_jsonl(_audit_root() / "calls.jsonl", public)


def _append_chunk(record: dict[str, Any], text: str) -> None:
    path = Path(record["_dir"]) / "chunks.jsonl"
    _append_jsonl(path, {"index": _chunk_index(path), "text": _redact_text(text)})


def _chunk_index(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("r", encoding="utf-8"))
    except FileNotFoundError:
        return 0


def _audit_root() -> Path:
    raw = os.getenv("LLM_AUDIT_OUTPUT_DIR") or ".omx/artifacts/llm-audit/llm_calls"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _call_output_dir(*, arm: str, chapter_number: int | None, phase: str, call_id: str) -> Path:
    chapter = f"chapter_{int(chapter_number):02d}" if chapter_number else "chapter_unknown"
    return _audit_root() / "by_chapter" / _safe_name(arm) / chapter / f"{_safe_name(phase)}_{_safe_name(call_id)}"


def _prompt_to_dict(prompt: Any) -> dict[str, Any]:
    return _redact_sensitive({
        "system": str(getattr(prompt, "system", "") or ""),
        "user": str(getattr(prompt, "user", prompt) or ""),
        "messages": _json_safe(prompt.to_messages()) if hasattr(prompt, "to_messages") else [],
    })


def _config_to_dict(config: Any) -> dict[str, Any]:
    return _redact_sensitive({
        "model": str(getattr(config, "model", "") or ""),
        "max_tokens": int(getattr(config, "max_tokens", 0) or 0),
        "temperature": float(getattr(config, "temperature", 0.0) or 0.0),
        "response_format": _json_safe(getattr(config, "response_format", None)),
    })


def _token_usage_to_dict(token_usage: Any) -> dict[str, Any]:
    if token_usage is None:
        return {}
    if hasattr(token_usage, "to_dict"):
        return _json_safe(token_usage.to_dict())
    input_tokens = int(getattr(token_usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(token_usage, "output_tokens", 0) or 0)
    cache_creation = int(getattr(token_usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(token_usage, "cache_read_input_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "total_tokens": int(getattr(token_usage, "total_tokens", 0) or input_tokens + output_tokens + cache_creation + cache_read),
    }


def _estimated_stream_usage(prompt: Any, chunks: list[str]) -> dict[str, Any]:
    prompt_text = json.dumps(_prompt_to_dict(prompt), ensure_ascii=False)
    output_text = "".join(chunks)
    return {
        "estimated": True,
        "input_tokens": _estimate_tokens(prompt_text),
        "output_tokens": _estimate_tokens(output_text),
        "total_tokens": _estimate_tokens(prompt_text) + _estimate_tokens(output_text),
    }


def _estimate_tokens(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", compact))
    return int(cjk / 1.5 + max(len(compact) - cjk, 0) / 4) + 1


def _infer_phase() -> str:
    for frame in inspect.stack()[2:40]:
        filename = frame.filename.replace("\\", "/")
        function = frame.function
        if "auto_novel_generation_workflow.py" in filename:
            if function == "generate_chapter_stream":
                return "chapter_generation_stream"
            if function == "generate_chapter":
                return "chapter_generation_beat"
            if function == "_suggest_outline":
                return "chapter_outline_suggestion"
        if "chapter_narrative_sync.py" in filename:
            return "chapter_narrative_sync"
        if "chapter_review_service.py" in filename:
            if "character" in function:
                return "chapter_review_character"
            if "timeline" in function:
                return "chapter_review_timeline"
            if "storyline" in function:
                return "chapter_review_storyline"
            if "foreshadow" in function:
                return "chapter_review_foreshadow"
        if "continuous_planning_service.py" in filename:
            return "chapter_outline_suggestion"
        if "autopilot_daemon.py" in filename:
            if "tension" in function or "audit" in function:
                return "chapter_narrative_sync"
            return "chapter_generation_beat"
    return "unknown"


def _infer_source() -> str:
    for frame in inspect.stack()[2:10]:
        filename = Path(frame.filename).name
        if filename:
            return f"{filename}:{frame.function}"
    return "unknown"


def _normalize_phase(phase: str) -> str:
    if phase.startswith("evolution_"):
        if phase in {"evolution_agent_control_card", "evolution_agent_reflection"}:
            return phase
        if phase in {"evolution_after_chapter_review", "evolution_reflection"}:
            return "evolution_agent_reflection"
        return "evolution_agent_control_card"
    return phase if phase in AUDIT_PHASES else "unknown"


def _infer_arm(novel_id: str) -> str:
    lowered = novel_id.lower()
    if "control" in lowered:
        return "control_off"
    if "experiment" in lowered:
        return "experiment_on"
    return ""


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        return str(value)


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"api_key", "apikey", "authorization", "headers", "cookies", "private_key"}:
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_sensitive(item) for item in value]
    return value


def _redact_text(text: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[REDACTED]", str(text or ""))
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;\]}]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        redacted,
        flags=re.DOTALL,
    )
    return redacted


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _hash_json(payload: Any) -> str:
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _payload_chars(payload: dict[str, Any]) -> int:
    return len(str(payload.get("system") or "")) + len(str(payload.get("user") or ""))


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("._")
    return safe[:120] or "unknown"


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def write_audit_inventory(audit_output_dir: str | Path | None = None) -> dict[str, Any]:
    """Write a human-readable inventory and machine manifest for an audit run."""

    root = Path(audit_output_dir).expanduser() if audit_output_dir else _audit_root()
    if not root.is_absolute():
        root = Path.cwd() / root
    calls_path = root / "calls.jsonl"
    records: list[dict[str, Any]] = []
    if calls_path.exists():
        for line in calls_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    by_arm_chapter: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    phase_counts: Counter[str] = Counter()
    missing_files: list[dict[str, Any]] = []
    for record in records:
        phase_counts[str(record.get("phase") or "unknown")] += 1
        arm = str(record.get("arm") or "unknown")
        chapter = record.get("chapter_number")
        chapter_key = f"chapter_{int(chapter):02d}" if chapter else "chapter_unknown"
        by_arm_chapter[(arm, chapter_key)].append(record)
        paths = record.get("paths") if isinstance(record.get("paths"), dict) else {}
        for kind in ("prompt", "output", "usage"):
            path = paths.get(kind)
            if path and not Path(path).exists():
                missing_files.append({"call_id": record.get("call_id"), "kind": kind, "path": path})
        if record.get("stream") and paths.get("chunks") and not Path(paths["chunks"]).exists():
            missing_files.append({"call_id": record.get("call_id"), "kind": "chunks", "path": paths["chunks"]})

    manifest = {
        "schema_version": 1,
        "run_id": os.getenv("LLM_AUDIT_RUN_ID") or (records[0].get("run_id") if records else ""),
        "audit_output_dir": str(root),
        "generated_at": _iso_now(),
        "total_calls": len(records),
        "phase_counts": dict(sorted(phase_counts.items())),
        "arms": sorted({str(record.get("arm") or "unknown") for record in records}),
        "chapters": {
            f"{arm}/{chapter}": len(items)
            for (arm, chapter), items in sorted(by_arm_chapter.items())
        },
        "missing_files": missing_files,
        "complete": bool(records) and not missing_files,
    }
    _write_json(root.parent / "frontend_pressure_manifest.json", manifest)

    lines = [
        "# LLM Call Inventory",
        "",
        f"- Run ID: `{manifest['run_id']}`",
        f"- Audit dir: `{root}`",
        f"- Total calls: `{len(records)}`",
        f"- Complete files: `{manifest['complete']}`",
        "",
        "## Phase Counts",
        "",
    ]
    if phase_counts:
        lines.extend(f"- `{phase}`: {count}" for phase, count in sorted(phase_counts.items()))
    else:
        lines.append("- No calls recorded.")
    lines.extend(["", "## Calls By Chapter", ""])
    for (arm, chapter), items in sorted(by_arm_chapter.items()):
        lines.append(f"### {arm} / {chapter}")
        lines.append("")
        for record in items:
            paths = record.get("paths") if isinstance(record.get("paths"), dict) else {}
            lines.append(
                "- "
                f"`{record.get('phase')}` `{record.get('call_id')}` "
                f"status=`{record.get('status')}` stream=`{record.get('stream')}` "
                f"prompt=`{paths.get('prompt', '')}` output=`{paths.get('output', '')}`"
            )
        lines.append("")
    if missing_files:
        lines.extend(["## Missing Files", ""])
        lines.extend(f"- `{item['call_id']}` missing `{item['kind']}`: `{item['path']}`" for item in missing_files)
        lines.append("")
    (root.parent / "llm_call_inventory.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description="Write an inventory for LLM audit artifacts.")
    parser.add_argument("--output-dir", default=os.getenv("LLM_AUDIT_OUTPUT_DIR") or ".omx/artifacts/llm-audit/llm_calls")
    args = parser.parse_args()
    manifest = write_audit_inventory(args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
