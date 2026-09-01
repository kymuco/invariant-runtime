from __future__ import annotations

from invariant.spec import BasePlugin


class NotesPlugin(BasePlugin):
    def initialize(self, *, storage, **deps) -> None:
        self._storage = storage

    def shutdown(self) -> None:
        self._storage = None

    def save(self, text: str) -> None:
        self._storage.write_note(text)

    def load(self) -> str:
        return self._storage.read_note()
