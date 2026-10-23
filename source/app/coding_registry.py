"""Coding agents and API key slots smartLarch Platform knows about (v4.0.0).

ONE place for the agent list, their CLI names, which API provider each one
maps to and which environment variables carry its key into a launched
terminal.  Provider URLs / models live in ``ai_review.PROVIDERS``.

Synced 2026-10-20 with AI Coding Desktop v1.3.2
(``AI coding platform/AI-Coding-Desktop``: ``bridge/paths.py`` CLI discovery,
``bridge/module_runtime.py`` key injection, ``bridge/config.py`` API slots)
and the Qwen Token Plan config in ``AI coding platform/QwenCloud-CLI/api-config``.
Things that library does not carry (provider base URLs except Qwen, the
Claude-desktop bundled CLI, protocol / appx launchers) stay smartLarch's own.

No Qt here - unit-testable on its own.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Display / iteration order (Settings rows, AI menu, Verify).
AGENTS: tuple[str, ...] = ("kimi", "grok", "codex", "claude", "qwen", "deepseek")

# Auto-pick order for the default AI when nothing is chosen.
AUTO_ORDER: tuple[str, ...] = ("kimi", "grok", "claude", "codex", "qwen", "deepseek")

# CLIs AI Check can drive with EVERY tool switched off (v4.4.0): Claude through
# `claude -p --tools ""`, Kimi through an agent file with `tools: []`.  Grok's
# CLI cannot be locked down (`--tools` needs a real tool name, read tools stay
# auto-approved, and the old `--always-approve` run reached MCP tools), and
# Codex / Qwen Code / DeepSeek Harness have no tested no-tool mode: those open
# in a terminal only.  Order = Settings row order (auto tries CLIs first).
AI_CHECK_CLIS: tuple[str, ...] = ("kimi", "claude")

# Agent -> API provider whose key it uses (``ai_review.PROVIDERS`` keys).
AGENT_PROVIDER: dict[str, str] = {
    "kimi": "moonshot",
    "grok": "xai",
    "codex": "openai",
    "claude": "anthropic",
    "qwen": "qwen",
    "deepseek": "deepseek",
}

# Settings -> API binding rows: (provider, i18n label key, QSettings key).
API_SLOTS: tuple[tuple[str, str, str], ...] = (
    ("anthropic", "api_anthropic", "coding_api_anthropic"),
    ("openai", "api_openai", "coding_api_openai"),
    ("moonshot", "api_moonshot", "coding_api_moonshot"),
    ("xai", "api_xai", "coding_api_xai"),
    ("deepseek", "api_deepseek", "coding_api_deepseek"),
    ("qwen", "api_qwen", "coding_api_qwen"),
)

# Environment variables that carry an agent's own key into its terminal.
# Qwen Code reads the Token Plan key under both names (AI Coding Desktop
# injects both); DeepSeek Harness (dsh) reads DEEPSEEK_API_KEY.
AGENT_ENV: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
    "kimi": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "grok": ("XAI_API_KEY", "GROK_API_KEY"),
    "qwen": ("BAILIAN_TOKEN_PLAN_API_KEY", "OPENAI_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
}

# File names that are the agent's coding CLI (not a desktop shell).
CLI_NAMES: dict[str, set[str]] = {
    "codex": {"codex", "codex.exe", "codex.cmd"},
    "claude": {"claude", "claude.exe", "claude.cmd"},
    "kimi": {"kimi", "kimi.exe", "kimi.cmd"},
    "grok": {"grok", "grok.exe", "grok.cmd", "agent.exe"},
    "qwen": {"qwen", "qwen.cmd", "qwen.exe", "qwen.bat"},
    "deepseek": {"dsh", "dsh.cmd", "dsh.ps1", "dsh.exe"},
}

# Agents whose coding CLI is preferred over any desktop / web client.
TERMINAL_FIRST: frozenset[str] = frozenset({"kimi", "grok", "qwen", "deepseek"})

# Official install pages (npm package pages for the two npm-distributed CLIs:
# @qwen-code/qwen-code self-updates; DeepSeek Harness is @deepseek-ai/dsh).
INSTALL_URLS: dict[str, str] = {
    "kimi": "https://code.kimi.com/",
    "grok": "https://x.ai/cli",
    "codex": "https://developers.openai.com/codex/cli/",
    "claude": "https://docs.anthropic.com/en/docs/claude-code/overview",
    "qwen": "https://www.npmjs.com/package/@qwen-code/qwen-code",
    "deepseek": "https://www.npmjs.com/package/@deepseek-ai/dsh",
}


def agent_for_provider(provider: str) -> str:
    """Reverse of AGENT_PROVIDER (first match), '' when unknown."""
    for agent, prov in AGENT_PROVIDER.items():
        if prov == provider:
            return agent
    return ""


def which_cli(name: str) -> Optional[str]:
    """``shutil.which`` that never returns a WindowsApps stub.

    ``%LOCALAPPDATA%\\Microsoft\\WindowsApps`` holds MSIX execution aliases;
    Claude's alias there points at a removed 2.1.128 install and dies on
    launch (AI Coding Desktop skips that folder for the same reason).
    """
    if not name or any(sep in name for sep in ("\\", "/")):
        return None
    exts = [""]
    if os.name == "nt" and not Path(name).suffix:
        exts += [e.lower() for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if e]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or "windowsapps" in folder.lower():
            continue
        for ext in exts:
            cand = Path(folder) / (name + ext)
            if cand.is_file():
                return str(cand)
    return None


def qwen_binary_ok(path: str) -> bool:
    """Only Qwen Code's own ``qwen`` launcher counts.

    QwenCloud CLI (account management, ``@qwencloud/qwencloud-cli``) and the
    ``launch-*`` helper scripts are not the coding agent.
    """
    p = str(path or "").replace("/", "\\").lower()
    name = p.rsplit("\\", 1)[-1]
    if "qwencloud" in p or name.startswith("launch-"):
        return False
    return name in CLI_NAMES["qwen"]
