"""Opt-in AI review helpers for completed EXAFS fits.

The module deliberately has no Qt dependency.  It builds a bounded,
traceable review payload and sends it only when the user presses AI Review.
API keys are supplied by the caller and never written to reports or logs.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AIReviewError(RuntimeError):
    """A display-safe review error that never contains an API key."""


PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "kind": "chat",
    },
    "anthropic": {
        "endpoint": "https://api.anthropic.com/v1/messages",
        "kind": "messages",
    },
    "moonshot": {
        "endpoint": "https://api.moonshot.ai/v1/chat/completions",
        "kind": "chat",
    },
    "xai": {
        "endpoint": "https://api.x.ai/v1/chat/completions",
        "kind": "chat",
    },
    # Alibaba Cloud Model Studio (Bailian) Token Plan, OpenAI-compatible.
    # The endpoint depends on the key's region (QWEN_BASE_URLS); keys of one
    # region are rejected by the other, plain dashscope.aliyuncs.com answers
    # 401 for Token Plan keys.  Source: AI coding platform/QwenCloud-CLI/
    # api-config (README-API-Config.md, settings.token-plan.json, 2026-10).
    "qwen": {
        "endpoint": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        "kind": "chat",
    },
    # DeepSeek, OpenAI-compatible (v4.4.0).  Source: api-docs.deepseek.com
    # (2026-10-23): base https://api.deepseek.com, POST /chat/completions with a
    # Bearer key, GET /models lists the models.
    "deepseek": {
        "endpoint": "https://api.deepseek.com/chat/completions",
        "kind": "chat",
    },
}

QWEN_BASE_URLS: dict[str, str] = {
    "intl": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "cn": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
}


def provider_url(provider: str, kind: str = "chat", region: str = "") -> str:
    """Chat (or ``kind="models"`` ping) URL; *region* only matters for Qwen."""
    name = str(provider or "").strip().lower()
    if name == "qwen":
        base = QWEN_BASE_URLS.get(str(region or "intl").strip().lower(), QWEN_BASE_URLS["intl"])
        return base + ("/models" if kind == "models" else "/chat/completions")
    if kind == "models":
        return _PING_ENDPOINTS.get(name, "")
    spec = PROVIDERS.get(name)
    return spec["endpoint"] if spec else ""


# The built-in defaults up to v3.19.x.  The old Settings page saved whatever
# the model box showed, so these ids sit in users' settings as if chosen;
# they are treated as "not chosen" and cleared (``MainWindow._ai_review_model``).
LEGACY_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "moonshot": "kimi-k3",
    "xai": "latest",
}


def provider_default_model(provider: str) -> str:
    """No built-in model any more (v4.0.0): models and their effort levels
    change too fast to hard-code.  The user picks one from the provider's own
    list (``fetch_models``, filled by Verify connection).  Returns "" for a
    known provider; unknown providers still raise."""
    if provider not in PROVIDERS:
        raise AIReviewError("Unknown AI review provider.")
    return ""


def downsample_curves(*series: Sequence[float], limit: int = 24) -> list[list[float]]:
    """Make aligned, endpoint-preserving numeric curve samples for a prompt."""
    if not series or limit < 2:
        return []
    n = min((len(values) for values in series), default=0)
    if n <= 0:
        return []
    count = min(n, int(limit))
    if count == 1:
        indices = [0]
    else:
        indices = [round(i * (n - 1) / (count - 1)) for i in range(count)]
    return [[float(values[i]) for values in series] for i in indices]


def build_fit_review_prompt(context: Mapping[str, Any], *, language: str = "zh") -> str:
    """Create a focused, evidence-bound request for a scientific fit review."""
    payload = json.dumps(dict(context), ensure_ascii=False, indent=2, default=str)
    if language == "zh":
        instructions = """你是一位审慎的 XAFS/EXAFS 拟合技术审阅者。只根据下面由程序产生的
拟合证据进行评价，不要虚构数据或文献。请用简体中文输出：
1. 总体诊断与证据强度；2. R-factor、Nidp/Nvary、参数不确定度和相关性的解读；
3. data/model/residual 曲线抽样中的可见问题；4. 具体可执行的修改建议（参数、路径、
窗口或数据质量），并标明每项的风险/优先级；5. 还需要什么实验或模型信息。
这是一份诊断建议，不是对拟合或科学结论的有效性证明。"""
    else:
        instructions = """You are a cautious XAFS/EXAFS fit technical reviewer. Use only the
machine-generated fit evidence below; do not invent data or citations. Provide: (1) an overall
diagnosis and evidence strength; (2) interpretation of R-factor, Nidp/Nvary, parameter
uncertainties, and correlations; (3) visible issues in the sampled data/model/residual curves;
(4) concrete, actionable changes to parameters, paths, windows, or data quality, with a priority
and risk for each; and (5) missing experimental or model information. This is diagnostic advice,
not a scientific validity certificate."""
    return f"{instructions}\n\n=== smartLarch Platform fit evidence ===\n{payload}\n=== End evidence ==="


def _chat_content(payload: Mapping[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIReviewError("The AI provider returned no review text.") from exc
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping) and part.get("text")
        ).strip()
    else:
        text = ""
    if not text:
        raise AIReviewError("The AI provider returned an empty review.")
    return text


def _anthropic_content(payload: Mapping[str, Any]) -> str:
    blocks = payload.get("content", []) if isinstance(payload, Mapping) else []
    text = "\n".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, Mapping) and block.get("type") == "text"
    ).strip()
    if not text:
        raise AIReviewError("The AI provider returned no review text.")
    return text


# Minimal authenticated model-list probe per provider (network + key check).
_PING_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "moonshot": "https://api.moonshot.ai/v1/models",
    "xai": "https://api.x.ai/v1/models",
    "deepseek": "https://api.deepseek.com/models",
}


# Model ids that are not chat models (embeddings, speech, images, ...): kept
# out of the picker.  Anything else the provider lists is offered as-is.
_NON_CHAT_MARKERS = (
    "embed", "tts", "whisper", "dall-e", "moderation", "transcribe",
    "speech", "audio", "image", "realtime", "rerank",
)


def _model_ids(payload: Any) -> list[str]:
    """Chat model ids from a /models listing, newest first."""
    rows = payload.get("data", []) if isinstance(payload, Mapping) else []
    found = []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("id"):
            continue
        mid = str(row["id"])
        if any(mark in mid.lower() for mark in _NON_CHAT_MARKERS):
            continue
        stamp = row.get("created", row.get("created_at", ""))
        found.append((str(stamp), mid))
    found.sort(key=lambda t: t[0], reverse=True)  # epoch ints and ISO dates both sort as text
    seen, out = set(), []
    for _stamp, mid in found:
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def fetch_models(
    provider: str,
    api_key: str,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = 12.0,
    region: str = "",
) -> tuple[bool, str, list[str]]:
    """Verify the key AND list the provider's chat models (newest first).

    ``(ok, detail, model_ids)``; *detail* is display-safe (never the key).
    A 200 answer is "ok" even when the body is not a model list (ids = []).
    """
    name = str(provider or "").strip().lower()
    url = provider_url(name, "models", region) or None
    if url is None:
        return False, "unknown provider", []
    if not str(api_key or "").strip():
        return False, "no API key saved", []
    if name == "anthropic":
        headers = {"x-api-key": str(api_key), "anthropic-version": "2023-06-01"}
    else:
        headers = {"Authorization": f"Bearer {api_key}"}
    response = None
    try:
        response = opener(Request(url, headers=headers, method="GET"), timeout=float(timeout))
        raw = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"key rejected (HTTP {exc.code})", []
        return False, f"HTTP {exc.code}", []
    except URLError:
        return False, "network unreachable", []
    except OSError:
        return False, "connection failed", []
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    try:
        ids = _model_ids(json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)))
    except (ValueError, UnicodeDecodeError):
        ids = []
    return True, (f"ok, {len(ids)} models" if ids else "ok"), ids


def ping_provider(
    provider: str,
    api_key: str,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = 12.0,
    region: str = "",
) -> tuple[bool, str]:
    """Verify one provider's key and network path with a minimal GET.

    Returns ``(ok, detail)`` where *detail* is display-safe — the key is used
    solely for the auth header and never appears in the message.  ``opener``
    is injectable so the transport stays testable offline.
    """
    ok, detail, _ids = fetch_models(provider, api_key, opener=opener, timeout=timeout, region=region)
    return ok, ("ok" if ok else detail)


def request_fit_review(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = 90.0,
    region: str = "",
    max_tokens: int = 1400,
) -> str:
    """Send one explicit fit-review request and return the model text.

    ``opener`` is injectable so transport behaviour is fully testable without a
    network call.  Keys are used solely for the Authorization header.
    ``max_tokens`` caps the Anthropic answer (that API requires a cap); AI
    Check passes a larger one so a many-dataset JSON answer is not cut off.
    """
    spec = PROVIDERS.get(str(provider or "").strip().lower())
    if spec is None:
        raise AIReviewError("Unknown AI review provider.")
    if not str(api_key or "").strip():
        raise AIReviewError("No API key is configured for the selected AI reviewer.")
    if not str(model or "").strip():
        raise AIReviewError("Set a model for the selected AI reviewer in Settings.")

    if spec["kind"] == "messages":
        headers = {
            "Content-Type": "application/json",
            "x-api-key": str(api_key),
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": str(model),
            "max_tokens": int(max_tokens),
            "system": "You provide evidence-bound XAFS/EXAFS fit review.",
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "model": str(model),
            "messages": [
                {"role": "system", "content": "You provide evidence-bound XAFS/EXAFS fit review."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        # ``store`` is explicitly supported by OpenAI.  Do not assume its
        # OpenAI-compatible peers accept the same privacy extension.
        if provider == "openai":
            body["store"] = False

    request = Request(
        provider_url(provider, "chat", region),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    response = None
    try:
        response = opener(request, timeout=float(timeout))
        raw = response.read().decode("utf-8")
        payload = json.loads(raw)
    except HTTPError as exc:
        raise AIReviewError(f"AI review request was rejected (HTTP {exc.code}).") from exc
    except URLError as exc:
        raise AIReviewError("AI review connection failed. Check network access and provider settings.") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AIReviewError("AI review returned an unreadable response.") from exc
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    return _anthropic_content(payload) if spec["kind"] == "messages" else _chat_content(payload)
