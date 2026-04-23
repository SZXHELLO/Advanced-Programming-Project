"""Message bus module for decoupled channel-agent communication."""

from minibot.bus.events import InboundMessage, OutboundMessage
from minibot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
