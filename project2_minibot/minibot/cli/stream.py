"""Streaming renderer for CLI output.

Uses Rich Live with auto_refresh=False for stable, flicker-free
markdown rendering during streaming. Ellipsis mode handles overflow.
"""

from __future__ import annotations

import sys
from contextlib import nullcontext

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from minibot import __display_name__, __logo__


def _make_console() -> Console:
    return Console(file=sys.stdout, force_terminal=True)


class ThinkingSpinner:
    """Spinner that shows '<display_name> is thinking...' with pause support.

    Implemented with Rich ``Console.status(..., spinner="dots")``. The animated
    glyph beside the text is Rich's *dots* spinner (often Braille patterns in the
    terminal); color follows the active console/theme (commonly green in
    PowerShell/cmd).
    """

    def __init__(self, console: Console | None = None):
        c = console or _make_console()
        self._spinner = c.status(
            f"[dim]{__display_name__} is thinking...[/dim]", spinner="dots"
        )
        self._active = False

    def __enter__(self):
        self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        self._spinner.stop()
        return False

    def pause(self):
        """Context manager: temporarily stop spinner for clean output."""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            if self._spinner and self._active:
                self._spinner.stop()
            try:
                yield
            finally:
                if self._spinner and self._active:
                    self._spinner.start()

        return _ctx()


class StreamRenderer:
    """Rich Live streaming with markdown. auto_refresh=False avoids render races.

    Deltas arrive pre-filtered (no <think> tags) from the agent loop.
    Each *delta* may contain several characters from the provider; we expand it
    to **one render refresh per Unicode scalar** so the CLI shows true
    character-by-character output (逐字), not batched chunks.

    Flow per round:
      spinner -> first visible delta -> header + Live renders ->
      on_end -> Live stops (content stays on screen)
    """

    def __init__(
        self,
        render_markdown: bool = True,
        show_spinner: bool = True,
        *,
        react_stream: bool = False,
    ):
        self._md = render_markdown
        self._show_spinner = show_spinner
        self._react_stream = react_stream
        self._buf = ""
        self._live: Live | None = None
        self.streamed = False
        self._spinner: ThinkingSpinner | None = None
        self._start_spinner()

    def _render(self):
        from minibot.utils.react_display import buffer_looks_react, format_react_text

        if self._buf and self._react_stream and buffer_looks_react(self._buf):
            return Text(format_react_text(self._buf))
        if self._md and self._buf:
            return Markdown(self._buf)
        return Text(self._buf or "")

    def _start_spinner(self) -> None:
        if self._show_spinner:
            self._spinner = ThinkingSpinner()
            self._spinner.__enter__()

    def _stop_spinner(self) -> None:
        if self._spinner:
            self._spinner.__exit__(None, None, None)
            self._spinner = None

    async def on_delta(self, delta: str) -> None:
        if not delta:
            return
        self.streamed = True
        for ch in delta:
            self._buf += ch
            if self._live is None:
                if not self._buf.strip():
                    continue
                self._stop_spinner()
                c = _make_console()
                c.print()
                c.print(f"[cyan]{__logo__} {__display_name__}[/cyan]")
                self._live = Live(self._render(), console=c, auto_refresh=False)
                self._live.start()
            assert self._live is not None
            self._live.update(self._render())
            self._live.refresh()

    async def on_end(self, *, resuming: bool = False) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.refresh()
            self._live.stop()
            self._live = None
        self._stop_spinner()
        if resuming:
            self._buf = ""
            self._start_spinner()
        else:
            _make_console().print()

    def stop_for_input(self) -> None:
        """Stop spinner before user input to avoid prompt_toolkit conflicts."""
        self._stop_spinner()

    def pause_spinner(self):
        """Temporarily hide the thinking spinner (e.g. before interleaved CLI lines)."""
        if self._spinner:
            return self._spinner.pause()
        return nullcontext()

    async def close(self) -> None:
        """Stop spinner/live without rendering a final streamed round."""
        if self._live:
            self._live.stop()
            self._live = None
        self._stop_spinner()
