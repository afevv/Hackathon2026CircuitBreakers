from __future__ import annotations

from typing import Any, Callable

Listener = Callable[[str], None]


class ObservableViewModel:
    """Minimal observable base: Views subscribe and get told which property changed.

    Streamlit re-runs top to bottom so it can simply read state; a CLI, Qt or
    web-socket view can subscribe() to react to changes.
    """

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _set(self, name: str, value: Any) -> None:
        """Set `_name` and notify only if the value actually changed."""
        private = f"_{name}"
        if getattr(self, private, None) != value:
            setattr(self, private, value)
            for listener in list(self._listeners):
                listener(name)
