"""Path router for OpenAPI path templates (``/todos/{todoId}``): a dict for paths without
parameters, a segment trie for the rest. Static segments win over parameters.

``new_router()`` gives the Rust router (``takibiapi._core.Router``) when the extension is built and
this Python one otherwise; both give the same results.
"""

from __future__ import annotations

from typing import Any

from . import schema


def new_router() -> Any:
    core = schema._core
    return core.Router() if core is not None else Router()


class _Node:
    __slots__ = ("children", "handlers", "param", "param_name")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.param: _Node | None = None
        self.param_name = ""
        self.handlers: dict[str, Any] = {}


class Router:
    def __init__(self) -> None:
        self.static: dict[str, dict[str, Any]] = {}  # path -> {method: entry}
        self.root = _Node()

    def add(self, method: str, path: str, entry: Any) -> None:
        method = method.upper()
        segments = _split(path)
        if not any(_is_param(s) for s in segments):
            by_method = self.static.setdefault("/" + "/".join(segments), {})
            if method in by_method:
                raise ValueError(f"{method} {path} is already registered")
            by_method[method] = entry
            return
        node = self.root
        for seg in segments:
            if _is_param(seg):
                name = seg[1:-1]
                if node.param is None:
                    node.param = _Node()
                    node.param_name = name
                elif node.param_name != name:
                    raise ValueError(f"conflicting parameter names in {path!r}: {{{node.param_name}}} vs {seg}")
                node = node.param
            else:
                node = node.children.setdefault(seg, _Node())
        if method in node.handlers:
            raise ValueError(f"{method} {path} is already registered")
        node.handlers[method] = entry

    def match(self, method: str, path: str) -> tuple[Any, dict[str, str]] | None:
        by_method = self.static.get(path)
        if by_method is None and (path.endswith("/") or "//" in path):
            by_method = self.static.get("/" + "/".join(_split(path)))
        if by_method is not None:
            entry = by_method.get(method)
            if entry is not None:
                return entry, {}
        params: dict[str, str] = {}
        entry = _match(self.root, _split(path), 0, method, params)
        return (entry, params) if entry is not None else None

    def allowed(self, path: str) -> list[str]:
        """The methods registered for ``path`` (for 405 Method Not Allowed)."""
        found: set[str] = set(self.static.get("/" + "/".join(_split(path)), {}))
        for method in _methods(self.root, _split(path), 0):
            found.add(method)
        return sorted(found)


def _is_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def _split(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _match(node: _Node, segments: list[str], i: int, method: str, params: dict[str, str]) -> Any:
    if i == len(segments):
        return node.handlers.get(method)
    seg = segments[i]
    child = node.children.get(seg)
    if child is not None:
        found = _match(child, segments, i + 1, method, params)
        if found is not None:
            return found
    if node.param is not None:
        params[node.param_name] = seg
        found = _match(node.param, segments, i + 1, method, params)
        if found is not None:
            return found
        del params[node.param_name]
    return None


def _methods(node: _Node, segments: list[str], i: int) -> set[str]:
    if i == len(segments):
        return set(node.handlers)
    out: set[str] = set()
    child = node.children.get(segments[i])
    if child is not None:
        out |= _methods(child, segments, i + 1)
    if node.param is not None:
        out |= _methods(node.param, segments, i + 1)
    return out
