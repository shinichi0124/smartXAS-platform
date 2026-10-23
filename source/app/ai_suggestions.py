"""Safe contract for terminal-driven AI suggestions for EXAFS fit controls.

The external coding CLI receives a bounded evidence prompt and writes a JSON
answer to a disposable cache file.  This module never executes model text: it
only accepts a small whitelisted parameter schema for the GUI to apply.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


class AISuggestionError(ValueError):
    """A display-safe error for absent or unsafe suggestion output."""


class ClaudeSignInRequired(AISuggestionError):
    """The Claude CLI is installed but not signed in (or the sign-in expired)."""


class KimiSignInRequired(AISuggestionError):
    """The Kimi Code CLI is installed but not signed in (or the sign-in expired)."""


class AICheckCancelled(AISuggestionError):
    """The user pressed Cancel while an AI Check was running."""


FIT_SCOPES = {"current", "marked", "all"}
_GDS_KINDS = {"vary", "fix", "expr", "skip"}
_ROOT_KEYS = {"summary", "fit_scope", "gds", "normalization", "feff", "reasons"}
_GDS_KEYS = {"name", "value", "kind", "expr", "min", "max"}
_SECTION_KEYS = {"summary", "parameters"}
_FEFF_KEYS = {"summary", "recommendations"}
_NORM_KEYS = {"e0_auto", "e0", "nnorm", "flatten", "pre1", "pre2", "norm1", "norm2", "energy_shift"}
_WINDOW_NAMES = {"Hanning", "Kaiser-Bessel", "Gaussian", "Sine", "Parzen"}
_PATH_KEYS = {"index", "use", "s02", "e0", "deltar", "sigma2"}
_WINDOW_KEYS = {"kmin", "kmax", "dk", "kwindow", "rmin", "rmax", "dr", "rwindow", "fit_kweights"}

# Used by Codex/Grok when their CLI supports schema-constrained output.  The
# parser below remains the authority: it makes the same safety checks for all
# CLIs, including text-only Kimi/Claude prompt modes.
AI_SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "fit_scope", "gds", "normalization", "feff", "reasons"],
    "properties": {
        "summary": {"type": "string"},
        "fit_scope": {"type": "string", "enum": sorted(FIT_SCOPES)},
        "gds": {"type": "object"},
        "normalization": {"type": "object"},
        "feff": {"type": "object"},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
}


def suggestion_schema_json() -> str:
    """Return a portable JSON schema file for capable command-line clients."""
    return json.dumps(AI_SUGGESTION_SCHEMA, ensure_ascii=False, indent=2)


def _legacy_build_suggestion_prompt(
    scope: str,
    contexts: Sequence[Mapping[str, Any]],
    *,
    language: str = "zh",
) -> str:
    """Build a bounded prompt requiring a machine-readable parameter patch."""
    scope = str(scope or "").strip().lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("Unknown AI-suggestion scope.")
    evidence = json.dumps(list(contexts), ensure_ascii=False, indent=2, default=str)
    scope_label = {
        "current": "current completed fit",
        "marked": "marked datasets with cached fits",
        "all": "all cached fits",
    }[scope]
    if language == "zh":
        instruction = (
            "你是谨慎的 XAFS/EXAFS 拟合技术审阅者。只可依据下方程序生成的证据提出建议，"
            "不要编造数据或文献。分析范围是：" + scope_label + "。\n"
            "最后必须只输出一个严格 JSON 对象，不要 Markdown、代码块或额外文字。JSON 必须含"
            " summary、fit_scope、gds、paths、windows、reasons 六个字段。只建议需要改变的"
            " GDS 行、路径行和拟合窗口；gds 可用 name/value/kind/expr/min/max，paths 只能用"
            " index/use/s02/e0/deltar/sigma2。不要给出命令、文件路径或任何其他字段。"
            "JSON 骨架为 {\"summary\": \"\", \"fit_scope\": \"current\", \"gds\": [], "
            "\"paths\": [], \"windows\": {}, \"reasons\": []}。这是诊断建议，不是科学结论有效性的证明。"
        )
    else:
        instruction = (
            "You are a cautious XAFS/EXAFS fit technical reviewer. Use only the "
            f"machine-generated evidence below. Your scope is {scope_label}. Do not invent "
            "data or citations. Your final answer must be one strict JSON object only: no "
            "Markdown, code fences, or prose outside JSON. It must contain summary, fit_scope, "
            "gds, paths, windows, and reasons. Suggest only changes: gds may use "
            "name/value/kind/expr/min/max; paths may use only index/use/s02/e0/deltar/sigma2. "
            "Do not output commands, file paths, or other fields. Use this JSON skeleton: "
            "{\"summary\": \"\", \"fit_scope\": \"current\", \"gds\": [], \"paths\": [], "
            "\"windows\": {}, \"reasons\": []}. This is diagnostic advice, not a scientific "
            "validity certificate."
        )
    return f"{instruction}\n\n=== smartLarch Platform fit evidence ===\n{evidence}\n=== End evidence ==="


def claude_cli_head(cli_path: str) -> list[str]:
    """Command prefix that runs a CLI .exe / .cmd / .ps1 on Windows (Claude, Kimi)."""
    path = str(cli_path)
    low = path.lower()
    if low.endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", path]
    if low.endswith(".ps1"):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    return [path]


def build_claude_headless_argv(cli_path: str, schema_json: str = "", model: str = "") -> list[str]:
    """argv for the confined Claude Code channel (v4.3.0).

    ``-p`` prints one answer and exits; ``--tools ""`` removes EVERY tool (no
    file, shell or web access), slash commands, MCP servers and session files
    are off too, so the model can only answer from the prompt it is given on
    stdin.  This is what makes the CLI safe to drive unattended.
    """
    argv = claude_cli_head(cli_path) + [
        "-p",
        "--tools", "",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "json",
    ]
    if schema_json:
        argv += ["--json-schema", schema_json]
    if str(model or "").strip():
        argv += ["--model", str(model).strip()]
    return argv


def parse_claude_headless_output(stdout: str, stderr: str = "", returncode: int = 0) -> str:
    """Answer text from ``claude -p --output-format json``; display-safe errors."""
    envelope = None
    for obj in _iter_json_objects(stdout or ""):
        if isinstance(obj, Mapping) and obj.get("type") == "result":
            envelope = obj
    if envelope is None:
        tail = ((stderr or stdout or "").strip().splitlines() or [f"exit {returncode}"])[-1]
        raise AISuggestionError(f"Claude CLI gave no result: {tail[:200]}")
    text = envelope.get("result")
    if envelope.get("is_error") or returncode != 0:
        msg = str(text or stderr or f"exit {returncode}").strip().splitlines()
        msg = (msg[0] if msg else "unknown error")[:240]
        low = msg.lower()
        if envelope.get("api_error_status") in (401, 403) or "authenticate" in low or "login" in low:
            msg += " — open a terminal, run `claude auth login`, finish the browser sign-in, and try again."
            raise ClaudeSignInRequired(f"Claude CLI error: {msg}")
        raise AISuggestionError(f"Claude CLI error: {msg}")
    structured = envelope.get("structured_output")
    if isinstance(structured, Mapping):
        return json.dumps(structured, ensure_ascii=False)
    if not isinstance(text, str) or not text.strip():
        raise AISuggestionError("Claude CLI returned an empty answer.")
    return text


def _run_confined_cli(
    argv: list[str],
    stdin_text: str | None,
    parse,
    *,
    label: str,
    cwd: str | None = None,
    timeout: float = 300.0,
    env: Mapping[str, str] | None = None,
    cancel: Any = None,
) -> str:
    """Run one confined CLI call and return ``parse(stdout, stderr, returncode)``.

    Polls every 0.3 s so ``cancel`` (a ``threading.Event``) can kill the whole
    process tree; :class:`AICheckCancelled` is raised then.
    """
    import subprocess
    import time

    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=cwd,
            env=dict(env) if env else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise AISuggestionError(f"{label} could not start: {exc}") from exc

    def _kill() -> None:
        try:
            if hasattr(subprocess, "CREATE_NO_WINDOW"):  # Windows: take the whole tree
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=15,
                )
            proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.communicate(timeout=5)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    deadline = time.monotonic() + float(timeout)
    first = True
    while True:
        try:
            stdout, stderr = proc.communicate(input=stdin_text if first else None, timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            first = False
            if cancel is not None and cancel.is_set():
                _kill()
                raise AICheckCancelled("AI Check cancelled.")
            if time.monotonic() >= deadline:
                _kill()
                raise AISuggestionError(f"{label} timed out after {int(timeout)} s.")
        except OSError as exc:
            _kill()
            raise AISuggestionError(f"{label} could not start: {exc}") from exc
    return parse(stdout, stderr, proc.returncode)


def run_claude_headless(
    cli_path: str,
    prompt: str,
    schema_json: str = "",
    model: str = "",
    cwd: str | None = None,
    timeout: float = 300.0,
    env: Mapping[str, str] | None = None,
    cancel: Any = None,
) -> str:
    """Run the confined Claude channel and return the raw answer text."""
    return _run_confined_cli(
        build_claude_headless_argv(cli_path, schema_json, model), prompt,
        parse_claude_headless_output, label="Claude CLI",
        cwd=cwd, timeout=timeout, env=env, cancel=cancel,
    )


# Kimi Code (v4.4.0): the AI Check prompt rides in the body of a throwaway agent
# file (Kimi uses that body as the system prompt), so a long evidence block never
# meets the Windows command-line limit; -p only carries this short task line.
KIMI_TASK = "Complete the AI Check described in your instructions. Output one strict JSON object only."


def kimi_agent_file_text(system_prompt: str) -> str:
    """Kimi Code agent definition with EVERY tool off.

    Kimi Code documents ``tools: []`` as "disables all tools", in print mode
    (``kimi -p``) too.  Checked on kimi 2.2.0 (2026-10-23) with a canary file:
    no read, no shell, no write, and the model reports no tools.
    """
    return (
        "---\n"
        "name: smartlarch-ai-check\n"
        "description: Confined XAFS reviewer for smartLarch AI Check; answers only from the given evidence.\n"
        "tools: []\n"
        "subagents: []\n"
        "---\n"
        + str(system_prompt).replace("\r\n", "\n")
        + "\n"
    )


def build_kimi_headless_argv(cli_path: str, agent_file: str) -> list[str]:
    """argv for the confined Kimi Code channel (``kimi -p`` + no-tool agent file)."""
    return claude_cli_head(cli_path) + [
        "-p", KIMI_TASK,
        "--agent-file", str(agent_file),
        "--output-format", "stream-json",
    ]


def parse_kimi_stream_output(stdout: str, stderr: str = "", returncode: int = 0) -> str:
    """Answer text from ``kimi -p --output-format stream-json``; display-safe errors."""
    answers = [
        obj.get("content") for obj in _iter_json_objects(stdout or "")
        if isinstance(obj, Mapping) and obj.get("role") == "assistant"
    ]
    answers = [a for a in answers if isinstance(a, str) and a.strip()]
    if returncode == 0 and answers:
        return "\n".join(answers)
    lines = [
        ln.strip() for ln in (stderr or "").splitlines()
        if ln.strip() and not ln.strip().lower().startswith("see log")
    ]
    msg = (lines[0] if lines else (f"exit {returncode}" if returncode else "empty answer"))[:240]
    low = msg.lower()
    if any(w in low for w in ("login", "log in", "sign in", "unauthor", "401", "credential", "authenticat")):
        raise KimiSignInRequired(f"Kimi CLI error: {msg} — open a terminal, run `kimi login`, and try again.")
    raise AISuggestionError(f"Kimi CLI error: {msg}")


def run_kimi_headless(
    cli_path: str,
    prompt: str,
    cwd: str | None = None,
    timeout: float = 300.0,
    env: Mapping[str, str] | None = None,
    cancel: Any = None,
) -> str:
    """Run the confined Kimi channel and return the raw answer text."""
    import os
    import tempfile

    folder = Path(cwd) if cwd else Path(tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="smartlarch_kimi_", suffix=".md", dir=str(folder))
    agent_file = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(kimi_agent_file_text(prompt))
        return _run_confined_cli(
            build_kimi_headless_argv(cli_path, str(agent_file)), None,
            parse_kimi_stream_output, label="Kimi CLI",
            cwd=str(folder), timeout=timeout, env=env, cancel=cancel,
        )
    finally:
        try:
            agent_file.unlink()
        except OSError:
            pass


def _iter_json_objects(text: str):
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] != "{":
            i += 1
            continue
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(value, Mapping):
            yield value
        i = max(end, i + 1)


def _is_suggestion_payload(value: Mapping[str, Any]) -> bool:
    if "datasets" in value and "scope" in value:
        return True
    if "gds" in value and ("fit_scope" in value or "scope" in value):
        return True
    # the v3.19.5 AI Check answer: datasets sit under "process", not at the root
    if "categories" in value and "scope" in value and "summary" in value:
        return True
    return False


def _unwrap_cli_envelope(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Grok --json-schema writes {text, structuredOutput, usage, ...} not the schema."""
    structured = value.get("structuredOutput")
    if isinstance(structured, Mapping) and _is_suggestion_payload(structured):
        return structured
    blob = value.get("text")
    envelope = any(k in value for k in ("stopReason", "sessionId", "structuredOutput", "requestId"))
    if envelope and isinstance(blob, str) and blob.strip():
        found = [obj for obj in _iter_json_objects(blob) if _is_suggestion_payload(obj)]
        with_rows = [
            obj for obj in found
            if (isinstance(obj.get("datasets"), list) and obj.get("datasets"))
            or (isinstance(obj.get("gds"), list) and obj.get("gds"))
        ]
        if with_rows:
            return with_rows[-1]
        if found:
            return found[-1]
    return value


def _extract_json(text: str) -> Mapping[str, Any]:
    text = str(text or "").strip().lstrip("\ufeff")
    if not text:
        raise AISuggestionError("AI suggestion cache is empty.")
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    candidates.append(text)
    parsed: list[Mapping[str, Any]] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate.strip().lstrip("\ufeff"))
        except (TypeError, ValueError):
            continue
        if isinstance(value, Mapping):
            parsed.append(_unwrap_cli_envelope(value))
    if not parsed:
        parsed = [_unwrap_cli_envelope(obj) for obj in _iter_json_objects(text)]
    ranked = [obj for obj in parsed if isinstance(obj, Mapping) and _is_suggestion_payload(obj)]
    with_rows = [
        obj for obj in ranked
        if (isinstance(obj.get("datasets"), list) and obj.get("datasets"))
        or (isinstance(obj.get("gds"), list) and obj.get("gds"))
    ]
    if with_rows:
        return with_rows[-1]
    if ranked:
        return ranked[-1]
    if parsed:
        return parsed[0]
    raise AISuggestionError("AI suggestion is not a readable JSON object.")


def _check_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise AISuggestionError(f"AI suggestion contains unsupported {label} field(s): {', '.join(sorted(unknown))}.")


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise AISuggestionError(f"AI suggestion {label} must be a number.")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise AISuggestionError(f"AI suggestion {label} must be a number.") from exc
    if not math.isfinite(out):
        raise AISuggestionError(f"AI suggestion {label} must be finite.")
    return out


def _short_text(value: Any, label: str, *, limit: int = 2000) -> str:
    if not isinstance(value, str):
        raise AISuggestionError(f"AI suggestion {label} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise AISuggestionError(f"AI suggestion {label} is too long.")
    return value


def _validate_gds(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise AISuggestionError("AI suggestion gds must be a list.")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise AISuggestionError("Each AI-suggested GDS change must be an object.")
        _check_keys(row, _GDS_KEYS, "GDS")
        name = _short_text(row.get("name", ""), "GDS name", limit=80)
        if not name:
            raise AISuggestionError("Each AI-suggested GDS change needs a name.")
        patch: dict[str, Any] = {"name": name}
        for key in ("value", "min", "max"):
            if key in row and row[key] is not None:
                patch[key] = _finite_number(row[key], f"GDS {key}")
        if "kind" in row:
            kind = _short_text(row["kind"], "GDS kind", limit=16).lower()
            if kind not in _GDS_KINDS:
                raise AISuggestionError("AI suggestion GDS kind is unsupported.")
            patch["kind"] = kind
        if "expr" in row:
            patch["expr"] = _short_text(row["expr"], "GDS expression", limit=256)
        if len(patch) == 1:
            raise AISuggestionError("AI-suggested GDS change has no values to apply.")
        if "min" in patch and "max" in patch and patch["min"] > patch["max"]:
            raise AISuggestionError("AI suggestion GDS minimum exceeds maximum.")
        out.append(patch)
    return out


def _validate_paths(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise AISuggestionError("AI suggestion paths must be a list.")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise AISuggestionError("Each AI-suggested path change must be an object.")
        _check_keys(row, _PATH_KEYS, "path")
        idx = row.get("index")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise AISuggestionError("AI suggestion path index must be a non-negative integer.")
        patch: dict[str, Any] = {"index": idx}
        if "use" in row:
            if not isinstance(row["use"], bool):
                raise AISuggestionError("AI suggestion path use must be true or false.")
            patch["use"] = row["use"]
        for key in ("s02", "e0", "deltar", "sigma2"):
            if key in row:
                patch[key] = _short_text(row[key], f"path {key}", limit=256)
        if len(patch) == 1:
            raise AISuggestionError("AI-suggested path change has no values to apply.")
        out.append(patch)
    return out


def _validate_windows(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AISuggestionError("AI suggestion windows must be an object.")
    _check_keys(value, _WINDOW_KEYS, "window")
    out: dict[str, Any] = {}
    ranges = {
        "kmin": (0.0, 15.0), "kmax": (0.0, 20.0), "dk": (0.0, 5.0),
        "rmin": (0.0, 8.0), "rmax": (0.0, 10.0), "dr": (0.0, 2.0),
    }
    for key, (low, high) in ranges.items():
        if key in value:
            num = _finite_number(value[key], f"window {key}")
            if not low <= num <= high:
                raise AISuggestionError(f"AI suggestion window {key} is outside the UI range.")
            out[key] = num
    for key in ("kwindow", "rwindow"):
        if key in value:
            name = _short_text(value[key], f"window {key}", limit=40)
            if name not in _WINDOW_NAMES:
                raise AISuggestionError(f"AI suggestion window {key} is unsupported.")
            out[key] = name
    if "fit_kweights" in value:
        weights = value["fit_kweights"]
        if not isinstance(weights, list) or any(isinstance(x, bool) or x not in (1, 2, 3) for x in weights):
            raise AISuggestionError("AI suggestion fit_kweights must use only 1, 2, and 3.")
        out["fit_kweights"] = list(dict.fromkeys(int(x) for x in weights))
    if "kmin" in out and "kmax" in out and out["kmin"] >= out["kmax"]:
        raise AISuggestionError("AI suggestion kmin must be below kmax.")
    if "rmin" in out and "rmax" in out and out["rmin"] >= out["rmax"]:
        raise AISuggestionError("AI suggestion rmin must be below rmax.")
    return out


def _legacy_parse_suggestion_response(text: str) -> dict[str, Any]:
    """Parse and strictly whitelist a CLI response before the GUI can apply it."""
    raw = _extract_json(text)
    _check_keys(raw, _ROOT_KEYS, "root")
    required = {"summary", "fit_scope", "gds", "paths", "windows", "reasons"}
    missing = required.difference(raw)
    if missing:
        raise AISuggestionError(f"AI suggestion misses required field(s): {', '.join(sorted(missing))}.")
    scope = _short_text(raw["fit_scope"], "fit_scope", limit=16).lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("AI suggestion fit_scope is unsupported.")
    reasons = raw["reasons"]
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise AISuggestionError("AI suggestion reasons must be a text list.")
    return {
        "summary": _short_text(raw["summary"], "summary"),
        "fit_scope": scope,
        "gds": _validate_gds(raw["gds"]),
        "paths": _validate_paths(raw["paths"]),
        "windows": _validate_windows(raw["windows"]),
        "reasons": [_short_text(item, "reason", limit=1000) for item in reasons],
    }


# v0.14 sectioned contract.  Kept below the legacy parser so older cached
# results fail closed instead of being silently applied to the wrong controls.
def build_suggestion_prompt(
    scope: str,
    contexts: Sequence[Mapping[str, Any]],
    *,
    language: str = "zh",
) -> str:
    scope = str(scope or "").strip().lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("Unknown AI-suggestion scope.")
    evidence = json.dumps(list(contexts), ensure_ascii=False, indent=2, default=str)
    scope_label = {
        "current": "current completed fit",
        "marked": "marked datasets with cached fits",
        "all": "all cached fits",
    }[scope]
    skeleton = (
        '{"summary":"","fit_scope":"current",'
        '"gds":{"summary":"","parameters":[]},'
        '"normalization":{"summary":"","parameters":{}},'
        '"feff":{"summary":"","recommendations":[]},"reasons":[]}'
    )
    if language == "zh":
        instruction = (
            f"你是谨慎的 XAFS/EXAFS 技术审阅者兼参数顾问。范围为 {scope_label}。只使用下方证据，禁止编造。"
            "这是一次统一任务：既要给出诊断评审（写在 summary 与 reasons 里：R-factor/Nidp、参数相关、曲线问题、"
            "优先修改项与风险），又要给出可应用的参数补丁。"
            "最终只能输出一个 strict JSON 对象。GDS 修改写入 gds.parameters；pre-edge/normalization "
            "修改写入 normalization.parameters。FEFF 是独立只读建议段，只能包含文字 recommendations，"
            "禁止 FEFF path 参数、命令或文件路径。JSON 骨架：" + skeleton
            + "。这是诊断建议，不是科学结论有效性证明。"
        )
    else:
        instruction = (
            f"You are a cautious XAFS/EXAFS technical reviewer and parameter advisor. Scope: {scope_label}. "
            "Use only the evidence below; do not invent data. Unified task: (1) diagnostic review in "
            "summary and reasons (R-factor/Nidp, correlations, curve issues, prioritized changes with risk); "
            "(2) applicable parameter patches. Output one strict JSON object only. Put GDS changes in "
            "gds.parameters and pre-edge/normalization changes in normalization.parameters. "
            "FEFF is a separate read-only advisory section with text recommendations only; "
            "never emit FEFF path patches, commands, or file paths. JSON skeleton: " + skeleton
            + ". This is diagnostic advice, not a scientific validity certificate."
        )
    return f"{instruction}\n\n=== smartLarch Platform fit evidence ===\n{evidence}\n=== End evidence ==="


def format_ai_assist_report(suggestion: Mapping[str, Any], *, language: str = "zh") -> str:
    """Human-readable unified report (review prose + section summaries) from AI Assist JSON."""
    zh = str(language or "").lower().startswith("zh")
    lines: list[str] = []
    title = "=== AI 辅助拟合（诊断 + 可应用参数）===" if zh else "=== AI Fit Assist (review + applicable params) ==="
    lines.append(title)
    lines.append(f"fit_scope: {suggestion.get('fit_scope', '')}")
    lines.append("")
    lines.append(("【总体诊断】" if zh else "[Overall diagnosis]"))
    lines.append(str(suggestion.get("summary") or ("（无）" if zh else "(none)")))
    reasons = suggestion.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append(("【要点 / 风险】" if zh else "[Key points / risks]"))
        for i, r in enumerate(reasons, 1):
            lines.append(f"  {i}. {r}")
    for key, label_zh, label_en in (
        ("gds", "GDS 建议", "GDS suggestions"),
        ("normalization", "归一化建议", "Normalization suggestions"),
        ("feff", "FEFF 建议（只读）", "FEFF advice (read-only)"),
    ):
        sec = suggestion.get(key) or {}
        if not isinstance(sec, Mapping):
            continue
        lines.append("")
        lines.append(f"【{label_zh if zh else label_en}】")
        lines.append(str(sec.get("summary") or ("（无）" if zh else "(none)")))
        if key == "gds":
            params = sec.get("parameters") or []
            if isinstance(params, list) and params:
                for p in params[:40]:
                    if isinstance(p, Mapping):
                        lines.append(
                            f"  - {p.get('name', '?')}: value={p.get('value', '')} "
                            f"kind={p.get('kind', '')} expr={p.get('expr', '')}"
                        )
        if key == "normalization":
            params = sec.get("parameters") or {}
            if isinstance(params, Mapping) and params:
                for k, v in params.items():
                    lines.append(f"  - {k} = {v}")
        if key == "feff":
            recs = sec.get("recommendations") or []
            for item in recs:
                lines.append(f"  - {item}")
    lines.append("")
    lines.append(
        "应用：勾选 GDS / 归一化 / FEFF 后点 Apply AI Data；应用后请再 Run Fit 验证。"
        if zh
        else "Apply: tick GDS / Normalization / FEFF then Apply AI Data; re-run Fit to verify."
    )
    return "\n".join(lines)


def _validate_section(value: Any, label: str, parameter_validator) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AISuggestionError(f"AI suggestion {label} must be an object.")
    _check_keys(value, _SECTION_KEYS, label)
    if set(value) != _SECTION_KEYS:
        raise AISuggestionError(f"AI suggestion {label} needs summary and parameters.")
    return {
        "summary": _short_text(value["summary"], f"{label} summary"),
        "parameters": parameter_validator(value["parameters"]),
    }


def _validate_normalization(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AISuggestionError("AI suggestion normalization parameters must be an object.")
    _check_keys(value, _NORM_KEYS, "normalization")
    out: dict[str, Any] = {}
    for key in ("e0_auto", "flatten"):
        if key in value:
            if not isinstance(value[key], bool):
                raise AISuggestionError(f"AI suggestion normalization {key} must be true or false.")
            out[key] = value[key]
    if "nnorm" in value:
        nnorm = value["nnorm"]
        if isinstance(nnorm, bool) or not isinstance(nnorm, int) or nnorm not in (0, 1, 2, 3):
            raise AISuggestionError("AI suggestion normalization nnorm must be 0, 1, 2, or 3.")
        out["nnorm"] = nnorm
    limits = {
        "e0": (-1_000_000.0, 1_000_000.0), "pre1": (-2000.0, 0.0),
        "pre2": (-1000.0, 100.0), "norm1": (-100.0, 2000.0),
        "norm2": (0.0, 5000.0), "energy_shift": (-1000.0, 1000.0),
    }
    for key, (low, high) in limits.items():
        if key in value:
            number = _finite_number(value[key], f"normalization {key}")
            if not low <= number <= high:
                raise AISuggestionError(f"AI suggestion normalization {key} is outside the safe range.")
            out[key] = number
    if "pre1" in out and "pre2" in out and out["pre1"] >= out["pre2"]:
        raise AISuggestionError("AI suggestion normalization pre1 must be below pre2.")
    if "norm1" in out and "norm2" in out and out["norm1"] >= out["norm2"]:
        raise AISuggestionError("AI suggestion normalization norm1 must be below norm2.")
    return out


def parse_suggestion_response(text: str) -> dict[str, Any]:
    raw = _extract_json(text)
    _check_keys(raw, _ROOT_KEYS, "root")
    if set(raw) != _ROOT_KEYS:
        missing = _ROOT_KEYS.difference(raw)
        raise AISuggestionError(f"AI suggestion misses required field(s): {', '.join(sorted(missing))}.")
    scope = _short_text(raw["fit_scope"], "fit_scope", limit=16).lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("AI suggestion fit_scope is unsupported.")
    feff = raw["feff"]
    if not isinstance(feff, Mapping):
        raise AISuggestionError("AI suggestion feff must be an object.")
    _check_keys(feff, _FEFF_KEYS, "FEFF")
    if set(feff) != _FEFF_KEYS or not isinstance(feff["recommendations"], list):
        raise AISuggestionError("AI suggestion FEFF needs summary and text recommendations.")
    reasons = raw["reasons"]
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise AISuggestionError("AI suggestion reasons must be a text list.")
    return {
        "summary": _short_text(raw["summary"], "summary"),
        "fit_scope": scope,
        "gds": _validate_section(raw["gds"], "GDS", _validate_gds),
        "normalization": _validate_section(raw["normalization"], "normalization", _validate_normalization),
        "feff": {
            "summary": _short_text(feff["summary"], "FEFF summary"),
            "recommendations": [_short_text(item, "FEFF recommendation", limit=1000) for item in feff["recommendations"]],
        },
        "reasons": [_short_text(item, "reason", limit=1000) for item in reasons],
    }


def ai_cli_install_command(agent: str) -> str:
    """Return a fixed HTTPS vendor guide; callers must not execute it as code."""
    from .coding_registry import INSTALL_URLS as commands

    try:
        return commands[str(agent).strip().lower()]
    except KeyError as exc:
        raise AISuggestionError("Unknown AI CLI installer.") from exc


def clear_analysis_cache(root: Path | str) -> int:
    """Delete only known smartLarch Platform cache files, preserving unrelated data."""
    root = Path(root)
    specs = {
        "history": ("fit_*.json",),
        "ai_reviews": ("review_*.txt", "review_*.json"),
        "ai_suggestions": (
            "suggestion_*_prompt.txt", "suggestion_*_schema.json", "suggestion_*_result.json", "suggestion_*_run.ps1",
            "ai_check_*_prompt.txt", "ai_check_*_schema.json", "ai_check_*_result.json", "ai_check_*_run.ps1",
        ),
    }
    removed = 0
    for folder, patterns in specs.items():
        base = root / folder
        if not base.is_dir():
            continue
        for pattern in patterns:
            for path in base.glob(pattern):
                if path.is_file():
                    path.unlink()
                    removed += 1
    return removed


NORMALIZATION_SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "scope", "datasets", "reasons"],
    "properties": {
        "summary": {"type": "string"},
        "scope": {"type": "string", "enum": sorted(FIT_SCOPES)},
        "datasets": {"type": "array", "items": {"type": "object"}},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
}


def normalization_schema_json() -> str:
    return json.dumps(NORMALIZATION_SUGGESTION_SCHEMA, ensure_ascii=False, indent=2)


def build_normalization_prompt(
    scope: str,
    contexts: Sequence[Mapping[str, Any]],
    *,
    language: str = "zh",
) -> str:
    """Build a normalization-only prompt with one result row per dataset."""
    scope = str(scope or "").strip().lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("Unknown normalization AI scope.")
    scope_label = {
        "current": "current dataset",
        "marked": "marked datasets",
        "all": "all datasets",
    }[scope]
    skeleton = (
        '{"summary":"","scope":"' + scope + '","datasets":['
        '{"name":"exact dataset name","summary":"","parameters":{}}],"reasons":[]}'
    )
    allowed = "e0_auto, e0, nnorm, flatten, pre1, pre2, norm1, norm2, energy_shift"
    if language == "zh":
        instruction = (
            f"你是 XAFS pre-edge/normalization 审阅者。范围为 {scope_label}。"
            "只评估归一化；每个数据组必须返回独立参数，name 必须与证据中的数据组名完全一致。"
            f"parameters 只允许：{allowed}。禁止拟合、路径、FEFF、GDS、后台命令或文件操作。"
            "证据说明：samples 为原始 μ(E)（吸收边 ±60 eV 内加密采样）；derived 是程序算好的诊断量——"
            "e0_by_derivative=一阶导数峰位（e0 候选）、pre_edge_line=当前 pre 窗线性拟合、"
            "edge_step_estimate=边跳估算、current_norm_post=当前归一化在 norm1–norm2 区间的均值与斜率"
            "（理想：均值≈1、斜率≈0）；samples_norm_current=当前参数下的归一化结果。"
            "请据此判断当前归一化是否合理，只建议需要改变的参数。只输出严格 JSON：" + skeleton
        )
    else:
        instruction = (
            f"Review XAFS pre-edge and normalization only for {scope_label}. Return an independent "
            "parameter object for each dataset and preserve each exact dataset name. Allowed parameter "
            f"names: {allowed}. Do not discuss fitting, path models, shell commands, or file operations. "
            "Evidence: samples are raw μ(E) with dense sampling within ±60 eV of the edge; derived holds "
            "program-computed diagnostics — e0_by_derivative (E0 candidate), pre_edge_line (linearity of "
            "the current pre-edge window), edge_step_estimate, and current_norm_post (mean and slope of "
            "the current normalized spectrum over norm1–norm2; ideal ≈ 1 and ≈ 0). samples_norm_current "
            "shows the spectrum under the current settings. Judge whether the current normalization is "
            "reasonable and suggest only parameters that need changing. Output strict JSON only: " + skeleton
        )
    evidence = json.dumps(list(contexts), ensure_ascii=False, indent=2, default=str)
    return f"{instruction}\n\n=== smartLarch Platform normalization evidence ===\n{evidence}\n=== End evidence ==="


def parse_normalization_response(text: str) -> dict[str, Any]:
    """Strictly parse the independent Auto-normalization AI cache."""
    raw = _extract_json(text)
    allowed_root = {"summary", "scope", "datasets", "reasons"}
    _check_keys(raw, allowed_root, "normalization root")
    if set(raw) != allowed_root:
        missing = allowed_root.difference(raw)
        raise AISuggestionError(f"Normalization AI suggestion misses: {', '.join(sorted(missing))}.")
    scope = _short_text(raw["scope"], "normalization scope", limit=16).lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("Normalization AI suggestion scope is unsupported.")
    rows = raw["datasets"]
    if not isinstance(rows, list):
        raise AISuggestionError("Normalization AI datasets must be a list.")
    parsed_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise AISuggestionError("Each normalization dataset suggestion must be an object.")
        _check_keys(row, {"name", "summary", "parameters"}, "normalization dataset")
        if set(row) != {"name", "summary", "parameters"}:
            raise AISuggestionError("Each normalization dataset needs name, summary, and parameters.")
        name = _short_text(row["name"], "normalization dataset name", limit=240)
        if not name or name in seen:
            raise AISuggestionError("Normalization dataset names must be non-empty and unique.")
        seen.add(name)
        parsed_rows.append({
            "name": name,
            "summary": _short_text(row["summary"], "normalization dataset summary"),
            "parameters": _validate_normalization(row["parameters"]),
        })
    reasons = raw["reasons"]
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise AISuggestionError("Normalization AI reasons must be a text list.")
    return {
        "summary": _short_text(raw["summary"], "normalization summary"),
        "scope": scope,
        "datasets": parsed_rows,
        "reasons": [_short_text(item, "normalization reason", limit=1000) for item in reasons],
    }


# ---------------------------------------------------------------------------
# v3.19.5: ONE AI Check contract (data processing + fitting, user categories).
# The Fit-only / Norm-only parsers above stay: their whitelists are reused here
# and older cached results must still fail closed.
# ---------------------------------------------------------------------------
AI_CHECK_CATEGORIES = ("process", "fit")
# Data-processing parameters: the normalization keys plus the background / FT
# window keys the import batch pipeline and Auto norm may also decide.
PROCESS_EXTRA_KEYS = {"nvict", "rbkg", "bkg_kmin", "bkg_kmax", "kmin", "kmax"}
AI_CHECK_ROOT_KEYS = {"summary", "scope", "categories", "process", "fit", "reasons"}
AI_CHECK_ROOT_REQUIRED = {"summary", "scope", "categories", "reasons"}


def _validate_process_parameters(value: Any) -> dict[str, Any]:
    """Whitelist + range-check one dataset patch from the process category."""
    if not isinstance(value, Mapping):
        raise AISuggestionError("AI Check process parameters must be an object.")
    allowed = set(_NORM_KEYS) | PROCESS_EXTRA_KEYS
    _check_keys(value, allowed, "process")
    # null = "no suggestion for this parameter" (models fill the whole skeleton with
    # nulls); drop it AFTER the key whitelist so unknown keys are still refused
    value = {k: v for k, v in value.items() if v is not None}
    out = dict(_validate_normalization({k: v for k, v in value.items() if k in _NORM_KEYS}))
    if "nvict" in value:
        nvict = value["nvict"]
        if isinstance(nvict, bool) or not isinstance(nvict, int) or nvict not in (0, 1, 2, 3):
            raise AISuggestionError("AI Check process nvict must be 0, 1, 2, or 3.")
        out["nvict"] = nvict
    limits = {
        "rbkg": (0.05, 5.0),
        "bkg_kmin": (0.0, 15.0),
        "bkg_kmax": (0.1, 25.0),
        "kmin": (0.0, 15.0),
        "kmax": (0.1, 25.0),
    }
    for key, (low, high) in limits.items():
        if key in value:
            number = _finite_number(value[key], f"process {key}")
            if not low <= number <= high:
                raise AISuggestionError(f"AI Check process {key} is outside the safe range.")
            out[key] = number
    if "bkg_kmin" in out and "bkg_kmax" in out and out["bkg_kmin"] >= out["bkg_kmax"]:
        raise AISuggestionError("AI Check process bkg_kmin must be below bkg_kmax.")
    if "kmin" in out and "kmax" in out and out["kmin"] >= out["kmax"]:
        raise AISuggestionError("AI Check process kmin must be below kmax.")
    return out


def _validate_process_section(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AISuggestionError("AI Check process section must be an object.")
    _check_keys(value, {"datasets"}, "process section")
    rows = value.get("datasets")
    if not isinstance(rows, list):
        raise AISuggestionError("AI Check process datasets must be a list.")
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise AISuggestionError("Each AI Check process dataset must be an object.")
        _check_keys(row, {"name", "summary", "parameters"}, "process dataset")
        if set(row) != {"name", "summary", "parameters"}:
            raise AISuggestionError("Each AI Check process dataset needs name, summary, and parameters.")
        name = _short_text(row["name"], "process dataset name", limit=240)
        if not name or name in seen:
            raise AISuggestionError("AI Check dataset names must be non-empty and unique.")
        seen.add(name)
        parsed.append({
            "name": name,
            "summary": _short_text(row["summary"], "process dataset summary"),
            "parameters": _validate_process_parameters(row["parameters"]),
        })
    return {"datasets": parsed}


def _validate_fit_section(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AISuggestionError("AI Check fit section must be an object.")
    _check_keys(value, {"gds", "feff"}, "fit section")
    out: dict[str, Any] = {}
    if "gds" in value:
        out["gds"] = _validate_gds(value["gds"])
    if "feff" in value:
        feff = value["feff"]
        if not isinstance(feff, Mapping):
            raise AISuggestionError("AI Check FEFF advice must be an object.")
        _check_keys(feff, _FEFF_KEYS, "FEFF")
        if set(feff) != _FEFF_KEYS or not isinstance(feff["recommendations"], list):
            raise AISuggestionError("AI Check FEFF advice needs summary and text recommendations.")
        out["feff"] = {
            "summary": _short_text(feff["summary"], "FEFF summary"),
            "recommendations": [
                _short_text(item, "FEFF recommendation", limit=1000)
                for item in feff["recommendations"]
            ],
        }
    return out


def parse_ai_check_response(text: str) -> dict[str, Any]:
    """Parse the single AI Check result; unrequested categories must be absent."""
    raw = _extract_json(text)
    _check_keys(raw, AI_CHECK_ROOT_KEYS, "root")
    missing = AI_CHECK_ROOT_REQUIRED.difference(raw)
    if missing:
        raise AISuggestionError(
            "AI Check misses required field(s): " + ", ".join(sorted(missing)) + "."
        )
    scope = _short_text(raw["scope"], "scope", limit=16).lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("AI Check scope is unsupported.")
    categories = raw["categories"]
    if not isinstance(categories, list) or any(not isinstance(c, str) for c in categories):
        raise AISuggestionError("AI Check categories must be a text list.")
    cats = [c.strip().lower() for c in categories]
    if any(c not in AI_CHECK_CATEGORIES for c in cats):
        raise AISuggestionError("AI Check categories must be process and/or fit.")
    reasons = raw["reasons"]
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise AISuggestionError("AI Check reasons must be a text list.")
    out: dict[str, Any] = {
        "summary": _short_text(raw["summary"], "summary"),
        "scope": scope,
        "categories": list(dict.fromkeys(cats)),
        "process": None,
        "fit": None,
        "reasons": [_short_text(item, "reason", limit=1000) for item in reasons],
    }
    if raw.get("process") is not None:
        if "process" not in cats:
            raise AISuggestionError("AI Check returned process data for an unrequested category.")
        out["process"] = _validate_process_section(raw["process"])
    if raw.get("fit") is not None:
        if "fit" not in cats:
            raise AISuggestionError("AI Check returned fit data for an unrequested category.")
        out["fit"] = _validate_fit_section(raw["fit"])
    return out


def build_ai_check_prompt(
    scope: str,
    categories: Sequence[str],
    evidence: Sequence[Mapping[str, Any]],
    *,
    language: str = "zh",
) -> str:
    """One prompt for the single AI Check (only the requested categories)."""
    scope = str(scope or "").strip().lower()
    if scope not in FIT_SCOPES:
        raise AISuggestionError("Unknown AI Check scope.")
    cats = [str(c).strip().lower() for c in (categories or []) if str(c).strip()]
    if not cats or any(c not in AI_CHECK_CATEGORIES for c in cats):
        raise AISuggestionError("AI Check needs at least one valid category.")
    scope_label = {
        "current": "the current dataset",
        "marked": "the marked datasets",
        "all": "all datasets",
    }[scope]
    # Only the requested categories carry a body; the rest are literally null.  A
    # skeleton that shows an empty body for an unrequested category gets copied
    # verbatim by the model and the parser (rightly) refuses it.
    process_part = (
        '{"datasets":[{"name":"exact dataset name","summary":"","parameters":{}}]}'
        if "process" in cats else "null"
    )
    fit_part = (
        '{"gds":[],"feff":{"summary":"","recommendations":[]}}' if "fit" in cats else "null"
    )
    skeleton = (
        '{"summary":"","scope":"' + scope + '","categories":['
        + ",".join('"' + c + '"' for c in cats)
        + '],"process":' + process_part + ',"fit":' + fit_part + ',"reasons":[]}'
    )
    allowed = (
        "e0_auto, e0, nnorm, flatten, pre1, pre2, norm1, norm2, nvict, rbkg, "
        "bkg_kmin, bkg_kmax, kmin, kmax"
    )
    if str(language or "").lower().startswith("zh"):
        instruction = (
            f"你是谨慎的 XAFS 技术审阅者。范围：{scope_label}。只使用下方程序生成的证据，禁止编造。\n"
            "本次只评估这些类别：" + ", ".join(cats) + "。未勾选的类别必须在 JSON 中缺省或为 null。\n"
            "process（数据处理）参数只允许：" + allowed
            + "；每个数据组返回独立参数，name 必须与证据中的组名完全一致。"
            "parameters 里只写你建议修改的键，不修改的键直接省略（不要写 null）。\n"
            "fit（拟合）只给 GDS 行补丁（name/value/kind/expr/min/max）与 FEFF 只读文字建议；"
            "禁止 FEFF 路径参数、命令或文件路径。\n"
            "证据：samples 为原始 μ(E) 边附近加密采样；derived 为程序算好的诊断量"
            "（e0_by_derivative、pre_edge_line、edge_step_estimate、current_norm_post）；"
            "samples_norm_current 为当前参数下的归一化结果；processing_record 为程序这一轮的"
            "处理记录；k_noise 为各条谱在高 k 的噪声估计。\n"
            "所有说明文字（summary、reasons、各组 summary）一律用简体中文。\n"
            "只输出一个严格 JSON 对象，不要 Markdown 代码围栏：" + skeleton
        )
    else:
        instruction = (
            f"You are a cautious XAFS technical reviewer. Scope: {scope_label}. Use only the "
            "program-generated evidence below; do not invent data.\n"
            "Evaluate ONLY these categories: " + ", ".join(cats)
            + ". Any category not requested MUST be absent or null in the JSON.\n"
            "process (data processing) parameters may use only: " + allowed
            + "; return independent parameters per dataset and preserve each exact dataset name. "
            "In parameters write only the keys you want to change and omit the rest (do not write null).\n"
            "fit may contain GDS row patches (name/value/kind/expr/min/max) and read-only FEFF "
            "text advice; never emit FEFF path patches, commands, or file paths.\n"
            "Evidence: samples are raw mu(E) with dense sampling near the edge; derived holds "
            "program-computed diagnostics (e0_by_derivative, pre_edge_line, edge_step_estimate, "
            "current_norm_post); samples_norm_current is the spectrum under the current settings; "
            "processing_record is this run's processing record; k_noise estimates the high-k "
            "noise of each spectrum.\n"
            "Write every text field (summary, reasons, dataset summaries) in English.\n"
            "Output one strict JSON object only, no Markdown fences: " + skeleton
        )
    evidence_text = json.dumps(list(evidence), ensure_ascii=False, indent=2, default=str)
    return (
        instruction
        + "\n\n=== smartLarch Platform AI Check evidence ===\n"
        + evidence_text
        + "\n=== End evidence ==="
    )


def format_ai_check_report(
    suggestion: Mapping[str, Any], *, language: str = "zh", channel: str = ""
) -> str:
    """Human-readable AI Check report for the categories the answer carries."""
    zh = str(language or "").lower().startswith("zh")
    cat_names = {"process": "数据处理", "fit": "拟合"} if zh else {"process": "data processing", "fit": "fit"}
    cat_list = [str(c) for c in (suggestion.get("categories") or [])]
    title = " + ".join(cat_names.get(c, c) for c in cat_list) or "-"
    lines: list[str] = []
    lines.append(f"=== AI Check（{title}）===" if zh else f"=== AI Check ({title}) ===")
    if channel:
        lines.append(("通道：" if zh else "Channel: ") + channel)
    cats = ", ".join(cat_list)
    lines.append(f"scope: {suggestion.get('scope', '')}   categories: {cats}")
    lines.append("")
    lines.append("【总体结论】" if zh else "[Summary]")
    lines.append(str(suggestion.get("summary") or ("（无）" if zh else "(none)")))
    reasons = suggestion.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append("【要点 / 风险】" if zh else "[Key points / risks]")
        for i, r in enumerate(reasons, 1):
            lines.append(f"  {i}. {r}")
    process = suggestion.get("process") or {}
    if isinstance(process, Mapping):
        for row in process.get("datasets") or []:
            if not isinstance(row, Mapping):
                continue
            lines.append("")
            lines.append(("【数据处理】" if zh else "[Data processing]") + " " + str(row.get("name") or "?"))
            if row.get("summary"):
                lines.append("  " + str(row["summary"]))
            for key, val in (row.get("parameters") or {}).items():
                lines.append(f"  - {key} = {val}")
    fit = suggestion.get("fit") or {}
    if isinstance(fit, Mapping):
        gds = fit.get("gds") or []
        if gds:
            lines.append("")
            lines.append("【拟合 GDS 建议】" if zh else "[Fit GDS suggestions]")
            for patch in gds[:40]:
                if isinstance(patch, Mapping):
                    lines.append(
                        f"  - {patch.get('name', '?')}: value={patch.get('value', '')} "
                        f"kind={patch.get('kind', '')} expr={patch.get('expr', '')}"
                    )
        feff = fit.get("feff") or {}
        if isinstance(feff, Mapping) and (feff.get("summary") or feff.get("recommendations")):
            lines.append("")
            lines.append("【FEFF 建议（只读）】" if zh else "[FEFF advice (read-only)]")
            lines.append(str(feff.get("summary") or ""))
            for item in feff.get("recommendations") or []:
                lines.append(f"  - {item}")
    lines.append("")
    lines.append(
        "点 AI Check 旁的「应用」会先显示对照表（当前值 → 建议值 → 理由），确认后才写入。"
        if zh else
        "Apply shows a comparison table (current -> suggested -> reason); nothing is written "
        "before you confirm."
    )
    return "\n".join(lines)
