import json
import re
import threading
from pathlib import Path

import pytest

from arkui_ut_agent.models import GLOBAL_MODEL_STATS


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-fire",
        action="store_true",
        default=False,
        help="Run fire tests (real API calls that cost money)",
    )


# Global lock for tests that modify global state - this works across threads
_global_stats_lock = threading.Lock()


@pytest.fixture
def reset_global_stats():
    """Reset global model stats and ensure exclusive access for tests that need it.

    This fixture should be used by any test that depends on global model stats
    to ensure thread safety and test isolation.
    """
    with _global_stats_lock:
        # Reset at start
        GLOBAL_MODEL_STATS._cost = 0.0  # noqa: SLF001
        GLOBAL_MODEL_STATS._n_calls = 0  # noqa: SLF001
        yield
        # Reset at end to clean up
        GLOBAL_MODEL_STATS._cost = 0.0  # noqa: SLF001
        GLOBAL_MODEL_STATS._n_calls = 0  # noqa: SLF001


def get_test_data(trajectory_name: str) -> dict[str, list[str]]:
    """Load test fixtures from a trajectory JSON file"""
    json_path = Path(__file__).parent / "test_data" / f"{trajectory_name}.traj.json"
    with json_path.open() as f:
        trajectory = json.load(f)

    # Extract model responses (assistant messages, starting from index 2)
    model_responses = []
    # Extract expected observations (user messages, starting from index 3)
    expected_observations = []

    for i, message in enumerate(trajectory):
        if i < 2:  # Skip system message (0) and initial user message (1)
            continue

        if message["role"] == "assistant":
            model_responses.append(message["content"])
        elif message["role"] == "user":
            expected_observations.append(message["content"])

    return {"model_responses": model_responses, "expected_observations": expected_observations}


def normalize_outputs(s: str) -> str:
    """Strip leading/trailing whitespace and normalize internal whitespace"""
    # Remove volatile command arguments before comparing observations.
    s = re.sub(r"<args>(.*?)</args>", "", s, flags=re.DOTALL)
    # Replace all lines that have root in them because they tend to appear with times
    s = "\n".join(l for l in s.split("\n") if "root root" not in l)
    return "\n".join(line.rstrip() for line in s.strip().split("\n"))


def assert_observations_match(expected_observations: list[str], messages: list[dict]) -> None:
    """Compare expected observations with actual observations from agent messages

    Args:
        expected_observations: List of expected observation strings
        messages: Agent conversation messages (list of message dicts with 'role' and 'content')
    """
    # Control-only assistant messages may appear between an action Observation and
    # the next action. Select actual Observation/exit roles instead of relying on
    # fixed alternating indices.
    actual_observations = [
        message["content"]
        for message in messages[2:]
        if message.get("role") in ("user", "exit")
    ]

    assert len(actual_observations) == len(expected_observations), (
        f"Expected {len(expected_observations)} observations, got {len(actual_observations)}"
    )

    for i, (expected_observation, actual_observation) in enumerate(zip(expected_observations, actual_observations)):
        normalized_actual = normalize_outputs(actual_observation)
        normalized_expected = normalize_outputs(expected_observation)

        assert normalized_actual == normalized_expected, (
            f"Step {i + 1} observation mismatch:\nExpected: {repr(normalized_expected)}\nActual: {repr(normalized_actual)}"
        )


@pytest.fixture
def local_test_data():
    """Load local test fixtures"""
    return get_test_data("local")
