# invariant/exec/store.py


class ManagedInstanceStore:
    """
    Global singleton store.
    Backed by registry.
    Keys MUST be canonical (e.g. id@version).
    """

    def __init__(self, *, registry):
        self._registry = registry

    def get(self, key: str) -> object | None:
        return self._registry.get_instance(key)

    def set(self, key: str, plugin: object) -> None:
        self._registry.set_instance(key, plugin)

    def drop(self, key: str) -> None:
        self._registry.drop_instance(key)

    def all_instances(self) -> dict[str, object]:
        # registry.instances() now exists
        return dict(self._registry.instances())

    def clear(self) -> None:
        """
        Clear managed instance cache WITHOUT calling shutdown().
        Intended for tests / dev resets. Lifecycle ownership is external.
        """
        clear_fn = getattr(self._registry, "clear_instances", None)
        if callable(clear_fn):
            clear_fn()


class EphemeralInstanceStore:
    """
    Execution-scoped instance store.
    Keys MUST be canonical (e.g. id@version).
    """

    def __init__(self):
        self._instances: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self._instances.get(key)

    def set(self, key: str, plugin: object) -> None:
        self._instances[key] = plugin

    def drop(self, key: str) -> None:
        self._instances.pop(key, None)

    def all_instances(self) -> dict[str, object]:
        return dict(self._instances)

    def clear(self) -> None:
        self._instances.clear()
        