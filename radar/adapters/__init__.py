"""
Adapter registry.

An adapter turns one source config entry into a list of `Posting` objects. Every
adapter registers itself under a short name that matches the `ats` field in
sources.json, so adding a new platform means writing one function and decorating
it — no changes anywhere else in the pipeline.

    @register("workable", requires=("token",))
    def fetch_workable(source): ...
"""

from __future__ import annotations

REGISTRY: dict[str, "AdapterSpec"] = {}


class AdapterSpec:
    __slots__ = ("name", "fn", "requires", "paginated")

    def __init__(self, name, fn, requires=(), paginated=False):
        self.name = name
        self.fn = fn
        self.requires = tuple(requires)
        self.paginated = paginated

    def validate(self, source: dict) -> list[str]:
        """Return a list of missing required config keys."""
        return [k for k in self.requires if not source.get(k)]

    def __call__(self, source):
        return self.fn(source)


def register(name, requires=(), paginated=False):
    def deco(fn):
        REGISTRY[name] = AdapterSpec(name, fn, requires, paginated)
        return fn
    return deco


def get(name):
    return REGISTRY.get(name)


def names():
    return sorted(REGISTRY)


# Import the modules so their @register decorators run. Kept at the bottom to
# avoid a circular import at module definition time.
from . import boards, workday, community  # noqa: E402,F401
