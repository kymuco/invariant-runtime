from __future__ import annotations

from invariant.spec import BasePlugin


class NotesStoragePlugin(BasePlugin):
    def initialize(self, *, fs, **deps) -> None:
        self._fs = fs

    def shutdown(self) -> None:
        self._fs = None

    def write_note(self, text: str) -> None:
        self._fs.write_text("notes/current.txt", text)

    def read_note(self) -> str:
        return self._fs.read_text("notes/current.txt")
