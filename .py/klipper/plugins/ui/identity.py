## Portable typed identities used by the Feather UI framework.

from enum import Enum


class FrameworkKey(Enum):
    """Base class for application-defined typed framework keys."""

    __key_namespace__ = None

    @property
    def symbol(self):
        owner = self.__class__
        namespace = getattr(owner, "__key_namespace__", None)
        if not namespace:
            namespace = "%s.%s" % (owner.__module__, owner.__qualname__)
        return "%s.%s" % (namespace, self.name)

    def __str__(self):
        return self.symbol


class PageKey(FrameworkKey):
    """Typed identity of a declarative page."""


class StateKey(FrameworkKey):
    """Typed identity of a state value."""


class CommandKey(FrameworkKey):
    """Typed identity of a semantic command."""


def serialize_key(value):
    if isinstance(value, FrameworkKey):
        return value.symbol
    if isinstance(value, Enum):
        return "%s.%s.%s" % (
            value.__class__.__module__, value.__class__.__qualname__, value.name)
    raise TypeError("Framework identity must be a typed enum member")
