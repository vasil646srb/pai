"""Storage facade: the public class used by the rest of the app."""

from __future__ import annotations

from .chat import ChatMixin
from .files import FilesMixin
from .guest_rooms import GuestRoomsMixin
from .inbox import InboxMixin
from .core import StorageCore
from .logs import LogsMixin
from .nodes import NodesMixin
from .wal import WalMixin


class Storage(StorageCore, NodesMixin, LogsMixin, WalMixin, ChatMixin, FilesMixin, InboxMixin, GuestRoomsMixin):
    """Thread-safe storage combining an append-only JSONL WAL with a SQLite index."""
