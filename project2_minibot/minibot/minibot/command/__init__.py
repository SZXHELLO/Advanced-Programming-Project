"""Slash command routing and built-in handlers."""

from minibot.command.builtin import register_builtin_commands
from minibot.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
