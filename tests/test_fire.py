#!/usr/bin/env python3
"""Fire tests: Real API integration tests that cost money.

################################################################################
#                                                                              #
#                         ⚠️  CRITICAL WARNING ⚠️                              #
#                                                                              #
#   THIS TEST FILE SHOULD NEVER BE RUN BY AN AI AGENT.                         #
#   IT REQUIRES EXPLICIT HUMAN REQUEST AND SUPERVISION.                        #
#                                                                              #
#   These tests make REAL API calls that:                                      #
#   - Cost real money (API usage fees)                                         #
#   - Require valid API keys for multiple providers                            #
#   - May have rate limits and quotas                                          #
#                                                                              #
#   To run: pytest tests/test_fire.py -v --run-fire                            #
#   Only run when explicitly requested by a human operator.                    #
#                                                                              #
################################################################################
"""

import os
import subprocess
import sys

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "fire: mark test as a fire test (real API calls)")


@pytest.fixture(autouse=True)
def skip_without_fire_flag(request):
    """Skip fire tests unless --run-fire is provided."""
    if not request.config.getoption("--run-fire", default=False):
        pytest.skip("Fire tests require --run-fire flag and cost real money")


SIMPLE_TASK = "Your job is to run `ls`, verify that you see files, then quit."

requires_openai = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")


def run_mini_command(extra_options: list[str]) -> subprocess.CompletedProcess:
    """Run the mini command with the given extra options."""
    cmd = [
        sys.executable,
        "-m",
        "arkui_ut_agent",
        "--exit-immediately",
        "-y",
        "--cost-limit",
        "0.03",
        "-t",
        SIMPLE_TASK,
        *extra_options,
    ]
    env = os.environ.copy()
    env["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "8"
    return subprocess.run(cmd, timeout=120, env=env)


# =============================================================================
# LiteLLM model
# =============================================================================


@requires_openai
def test_litellm_toolcall():
    """Test with litellm_toolcall model class."""
    result = run_mini_command(["--model", "openai/gpt-5.2"])
    assert result.returncode == 0


@requires_openai
def test_litellm_toolcall_explicit():
    """Test with litellm_toolcall model class."""
    result = run_mini_command(["--model", "openai/gpt-5.2", "--model-class", "litellm", "-c", "mini"])
    assert result.returncode == 0
