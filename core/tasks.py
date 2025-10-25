"""In-memory registry that keeps track of ongoing download tasks."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import token_hex
from typing import Dict, Optional


@dataclass
class DownloadTask:
    """Represents a single user request to download media from a URL.

    Attributes:
        url: Original URL requested by the user.
        uses_caption: ``True`` when the associated Telegram message stores its
            status inside a caption (e.g. inline placeholder photo). ``False``
            indicates a plain text message where the text must be edited.
        chat_id: Identifier of the chat containing the status message. ``None``
            for inline messages.
        message_id: Identifier of the status message inside the chat.
        inline_message_id: Inline message identifier that Telegram provides for
            inline results.
    """

    url: str
    uses_caption: bool
    chat_id: Optional[int] = None
    message_id: Optional[int] = None
    inline_message_id: Optional[str] = None


class TaskRegistry:
    """Container that assigns IDs to tasks and stores their metadata."""

    def __init__(self) -> None:
        self._tasks: Dict[str, DownloadTask] = {}

    def create(self, url: str, *, uses_caption: bool) -> str:
        """Create and remember a new task for ``url``.

        Returns the generated task identifier that is safe to embed into
        callback data. Collisions are improbable thanks to the 4-byte random
        token.
        """

        task_id = token_hex(4)
        while task_id in self._tasks:
            task_id = token_hex(4)
        self._tasks[task_id] = DownloadTask(url=url, uses_caption=uses_caption)
        return task_id

    def get(self, task_id: str) -> Optional[DownloadTask]:
        """Retrieve the stored task metadata for ``task_id`` if available."""

        return self._tasks.get(task_id)

    def attach_message(
        self,
        task_id: str,
        *,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
    ) -> None:
        """Update the location information for the task's status message."""

        task = self._tasks.get(task_id)
        if not task:
            return
        if chat_id is not None:
            task.chat_id = chat_id
        if message_id is not None:
            task.message_id = message_id
        if inline_message_id is not None:
            task.inline_message_id = inline_message_id

    def forget(self, task_id: str) -> None:
        """Remove ``task_id`` from the registry once it is finished."""

        self._tasks.pop(task_id, None)


TASKS = TaskRegistry()
