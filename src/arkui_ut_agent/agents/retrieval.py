"""Deterministic routing for already-classified retrieval needs."""

from enum import Enum
from types import MappingProxyType


class RetrievalIntent(str, Enum):
    """An explicit information need selected before routing."""

    FIND_COMPONENT = "find_component"
    FIND_SOURCE = "find_source"
    FIND_TEST = "find_test"
    READ_IMPLEMENTATION = "read_implementation"
    SEARCH_TEXT = "search_text"
    RESOLVE_SYMBOL = "resolve_symbol"
    FIND_REFERENCES = "find_references"
    FIND_CALLERS = "find_callers"
    FIND_CALLEES = "find_callees"
    FIND_IMPLEMENTATIONS = "find_implementations"


class RetrievalDestination(str, Enum):
    """The project-owned tool boundary that should receive an intent."""

    KB = "kb_search"
    SOURCE_READ = "read_file"
    TEXT_SEARCH = "rg_search"
    SEMANTIC = "semantic_provider"


_DESTINATION_BY_INTENT = MappingProxyType(
    {
        RetrievalIntent.FIND_COMPONENT: RetrievalDestination.KB,
        RetrievalIntent.FIND_SOURCE: RetrievalDestination.KB,
        RetrievalIntent.FIND_TEST: RetrievalDestination.KB,
        RetrievalIntent.READ_IMPLEMENTATION: RetrievalDestination.SOURCE_READ,
        RetrievalIntent.SEARCH_TEXT: RetrievalDestination.TEXT_SEARCH,
        RetrievalIntent.RESOLVE_SYMBOL: RetrievalDestination.SEMANTIC,
        RetrievalIntent.FIND_REFERENCES: RetrievalDestination.SEMANTIC,
        RetrievalIntent.FIND_CALLERS: RetrievalDestination.SEMANTIC,
        RetrievalIntent.FIND_CALLEES: RetrievalDestination.SEMANTIC,
        RetrievalIntent.FIND_IMPLEMENTATIONS: RetrievalDestination.SEMANTIC,
    }
)


class RetrievalRouter:
    """Map one explicit retrieval intent to one destination without reasoning or I/O."""

    __slots__ = ()

    def route(self, intent: RetrievalIntent) -> RetrievalDestination:
        """Return the fixed destination for a validated intent.

        Raw strings and other objects are rejected so unsupported input cannot be
        silently reinterpreted or guessed by the routing layer.
        """
        if not isinstance(intent, RetrievalIntent):
            raise TypeError("RetrievalRouter.route requires a RetrievalIntent.")
        return _DESTINATION_BY_INTENT[intent]


__all__ = ["RetrievalDestination", "RetrievalIntent", "RetrievalRouter"]
