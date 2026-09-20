import ast
import inspect

import pytest

from arkui_ut_agent.agents import (
    RetrievalDestination,
    RetrievalIntent,
    RetrievalRouter,
)

EXPECTED_ROUTES = {
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


@pytest.mark.parametrize(("intent", "expected"), EXPECTED_ROUTES.items())
def test_every_supported_intent_maps_to_its_fixed_destination(intent, expected):
    assert RetrievalRouter().route(intent) is expected


def test_contract_distinguishes_all_retrieval_layers():
    router = RetrievalRouter()

    assert router.route(RetrievalIntent.FIND_COMPONENT) is RetrievalDestination.KB
    assert router.route(RetrievalIntent.READ_IMPLEMENTATION) is RetrievalDestination.SOURCE_READ
    assert router.route(RetrievalIntent.SEARCH_TEXT) is RetrievalDestination.TEXT_SEARCH
    assert router.route(RetrievalIntent.FIND_REFERENCES) is RetrievalDestination.SEMANTIC
    assert {destination.value for destination in RetrievalDestination} == {
        "kb_search",
        "read_file",
        "rg_search",
        "semantic_provider",
    }


@pytest.mark.parametrize("invalid", [None, "find_component", "unknown", 1, object()])
def test_invalid_or_unsupported_router_input_fails_explicitly(invalid):
    with pytest.raises(TypeError, match="requires a RetrievalIntent"):
        RetrievalRouter().route(invalid)

    with pytest.raises(ValueError):
        RetrievalIntent("unsupported_intent")


@pytest.mark.parametrize("intent", RetrievalIntent)
def test_same_input_always_returns_the_same_route(intent):
    router = RetrievalRouter()

    first = router.route(intent)
    repeated = [router.route(intent) for _ in range(5)]

    assert all(destination is first for destination in repeated)


def test_router_is_stateless_and_has_no_planner_or_model_dependency():
    router = RetrievalRouter()
    module = inspect.getmodule(RetrievalRouter)

    assert RetrievalRouter.__slots__ == ()
    assert not hasattr(router, "__dict__")
    assert module is not None
    assert tuple(inspect.signature(RetrievalRouter).parameters) == ()

    imported_modules = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(module)))
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_modules == {"enum", "types"}
