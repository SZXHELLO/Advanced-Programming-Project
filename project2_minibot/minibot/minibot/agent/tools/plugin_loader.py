"""Dynamic local tool plugin loader.

Extension requirement: allow registering new tools without modifying
the core agent loop.

Plugin author workflow (minimal convention):
1. Create a python module (e.g. `my_pkg.my_tools`)
2. Import `Tool` subclasses and annotate them with `@tool_plugin`
3. List the module path in config `tools.tool_plugins`

The loader instantiates matching tool classes and registers them into the
provided `ToolRegistry`.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from minibot.agent.tools.base import Tool
from minibot.agent.tools.registry import ToolRegistry


def tool_plugin(cls: type[Tool]) -> type[Tool]:
    """Decorator to mark a Tool subclass as a plugin tool."""
    setattr(cls, "__minibot_tool_plugin__", True)
    return cls


def _pick_constructor_kwargs(
    tool_cls: type[Tool],
    *,
    workspace: Path,
    allowed_dir: Path | None,
    extra_allowed_dirs: list[Path] | None,
) -> dict[str, Any]:
    """Best-effort map common constructor params to values."""
    sig = inspect.signature(tool_cls.__init__)
    kwargs: dict[str, Any] = {}

    for name, param in sig.parameters.items():
        if name in {"self"}:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.default is not inspect.Parameter.empty:
            # If it has a default we can still pass, but we only pass the ones
            # we can reliably populate.
            pass

        if name == "workspace":
            kwargs[name] = workspace
        elif name == "allowed_dir":
            if allowed_dir is not None:
                kwargs[name] = allowed_dir
        elif name == "extra_allowed_dirs":
            if extra_allowed_dirs is not None:
                kwargs[name] = extra_allowed_dirs
        elif name == "restrict_to_workspace":
            # For tools that accept this flag, keep it aligned with allowed_dir presence.
            kwargs[name] = allowed_dir is not None

    return kwargs


def _instantiate_plugin_tool(
    tool_cls: type[Tool],
    *,
    workspace: Path,
    allowed_dir: Path | None,
    extra_allowed_dirs: list[Path] | None,
) -> Tool | None:
    try:
        kwargs = _pick_constructor_kwargs(
            tool_cls,
            workspace=workspace,
            allowed_dir=allowed_dir,
            extra_allowed_dirs=extra_allowed_dirs,
        )
        return tool_cls(**kwargs)
    except Exception as e:
        logger.warning(
            "Failed to instantiate tool plugin {}: {}: {}", tool_cls, type(e).__name__, e
        )
        return None


def _iter_marked_tool_classes(module: Any) -> Iterable[type[Tool]]:
    for _, obj in inspect.getmembers(module, inspect.isclass):
        try:
            if not issubclass(obj, Tool):
                continue
        except Exception:
            continue
        if getattr(obj, "__minibot_tool_plugin__", False):
            yield obj


def load_tool_plugins(
    module_paths: list[str] | None,
    registry: ToolRegistry,
    *,
    workspace: Path,
    allowed_dir: Path | None,
    extra_allowed_dirs: list[Path] | None = None,
) -> None:
    """Load and register tool plugins into *registry*."""
    if not module_paths:
        return

    for module_path in module_paths:
        try:
            module = importlib.import_module(module_path)
        except Exception as e:
            logger.warning("Failed to import tool plugin module {}: {}: {}", module_path, type(e).__name__, e)
            continue

        # Convention: module can expose a `register_tools` callback.
        register = getattr(module, "register_tools", None)
        if callable(register):
            try:
                sig = inspect.signature(register)
                kwargs: dict[str, Any] = {}
                for name, param in sig.parameters.items():
                    if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                        continue
                    if name == "registry":
                        kwargs[name] = registry
                    elif name == "workspace":
                        kwargs[name] = workspace
                    elif name == "allowed_dir":
                        kwargs[name] = allowed_dir
                    elif name == "extra_allowed_dirs":
                        kwargs[name] = extra_allowed_dirs
                register(**kwargs)
            except Exception as e:
                logger.warning(
                    "tool plugin module {} register_tools failed: {}: {}",
                    module_path,
                    type(e).__name__,
                    e,
                )

        # Decorator-based convention (minimal).
        for tool_cls in _iter_marked_tool_classes(module):
            tool = _instantiate_plugin_tool(
                tool_cls,
                workspace=workspace,
                allowed_dir=allowed_dir,
                extra_allowed_dirs=extra_allowed_dirs,
            )
            if tool is None:
                continue
            registry.register(tool)
            logger.info("Registered tool plugin: {}", tool.name)

