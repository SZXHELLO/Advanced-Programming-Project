"""
minibot - A lightweight AI agent framework
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
import tomllib

import logging
import sys
import types


def _install_loguru_compat_logger() -> None:
    """Provide a minimal `loguru.logger` fallback when loguru isn't installed.

    This repo imports `from loguru import logger` in many modules. Some execution
    environments running the source tree may not have the `loguru` dependency.
    """

    class _CompatLogger:
        def __init__(self, base: logging.Logger) -> None:
            self._base = base

        def _format(self, msg: str, args: tuple[object, ...]) -> str:
            try:
                return msg.format(*args)
            except Exception:
                return msg

        def enable(self, *_args: object, **_kwargs: object) -> None:
            # No-op: standard logging levels are not mapped one-to-one.
            return

        def disable(self, *_args: object, **_kwargs: object) -> None:
            # No-op
            return

        def debug(self, msg: str, *args: object, **_kwargs: object) -> None:
            self._base.debug(self._format(msg, args))

        def info(self, msg: str, *args: object, **_kwargs: object) -> None:
            self._base.info(self._format(msg, args))

        def warning(self, msg: str, *args: object, **_kwargs: object) -> None:
            self._base.warning(self._format(msg, args))

        def exception(self, msg: str, *args: object, **_kwargs: object) -> None:
            self._base.exception(self._format(msg, args))

    base = logging.getLogger("minibot")
    if not base.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        base.addHandler(handler)
    base.setLevel(logging.INFO)

    loguru_mod = types.ModuleType("loguru")
    loguru_mod.logger = _CompatLogger(base)
    sys.modules["loguru"] = loguru_mod


try:
    import loguru as _loguru  # noqa: F401
except ModuleNotFoundError:
    _install_loguru_compat_logger()


def _install_tiktoken_compat() -> None:
    """Provide a minimal `tiktoken` fallback when dependency is missing.

    The project uses `tiktoken.get_encoding(...).encode(text)` to estimate
    prompt token sizes. For environments without tiktoken installed, we
    provide a coarse approximation based on character length.
    """

    class _CompatEncoding:
        def encode(self, text: str) -> list[int]:
            if not text:
                return []
            # Rough heuristic: ~4 chars per token for English-ish text.
            approx_tokens = max(1, len(text) // 4)
            return [0] * approx_tokens

    def get_encoding(_name: str) -> _CompatEncoding:
        return _CompatEncoding()

    tiktoken_mod = types.ModuleType("tiktoken")
    tiktoken_mod.get_encoding = get_encoding  # type: ignore[attr-defined]
    sys.modules["tiktoken"] = tiktoken_mod


try:
    import tiktoken as _tiktoken  # noqa: F401
except ModuleNotFoundError:
    _install_tiktoken_compat()


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("minibot-ai")
    except PackageNotFoundError:
        # Source checkouts often import minibot without installed dist-info.
        return _read_pyproject_version() or "0.1.5.post1"


__version__ = _resolve_version()
__logo__ = "🪁"
# User-facing product name shown in CLI banners, spinners, status lines, etc.
__display_name__ = "minibot"

from minibot.minibot import Minibot, RunResult

__all__ = ["Minibot", "RunResult", "__display_name__", "__version__", "__logo__"]
