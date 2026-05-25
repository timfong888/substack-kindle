"""Tests for the per-run deployment entrypoint (SAT-256 / #20, PRD §Deployment).

Acceptance:
- The pipeline runs as a short scheduled burst (one pass); no held-open session.
- Credentials come from the runtime environment, never committed.
- Logic is portable (injected collaborators), so it translates to the self-hosted
  Claude Agent SDK without a rewrite.
"""

import re

import pytest

import substack_kindle.runner as runner_mod
from substack_kindle.runner import RuntimeConfig, load_runtime_config, run_once

FULL_ENV = {
    "POSTMARK_SERVER_TOKEN": "tok-123",
    "WHITELIST_EMAIL": "kindle-system@whitelist.example",
}


def test_load_runtime_config_reads_env():
    config = load_runtime_config(FULL_ENV)
    assert isinstance(config, RuntimeConfig)
    assert config.postmark_server_token == "tok-123"
    assert config.whitelist_email == "kindle-system@whitelist.example"


@pytest.mark.parametrize("missing", ["POSTMARK_SERVER_TOKEN", "WHITELIST_EMAIL"])
def test_missing_required_config_raises_listing_key(missing):
    env = {k: v for k, v in FULL_ENV.items() if k != missing}
    with pytest.raises(RuntimeError) as exc:
        load_runtime_config(env)
    assert missing in str(exc.value)


def test_blank_value_counts_as_missing():
    env = dict(FULL_ENV, POSTMARK_SERVER_TOKEN="")
    with pytest.raises(RuntimeError):
        load_runtime_config(env)


def test_repr_redacts_server_token():
    config = load_runtime_config(FULL_ENV)
    assert "tok-123" not in repr(config)
    assert "redacted" in repr(config).lower()


def test_run_once_is_a_single_burst():
    calls = []

    def fake_run():
        calls.append(1)
        return "job-result"

    result = run_once(run_pipeline=fake_run)
    assert result == "job-result"
    assert calls == [1]  # exactly one pass — no loop, no held-open session


def test_run_once_propagates_pipeline_result():
    assert run_once(run_pipeline=lambda: {"outcome": "delivered"}) == {"outcome": "delivered"}


def test_module_has_no_hardcoded_credentials():
    with open(runner_mod.__file__, encoding="utf-8") as fh:
        source = fh.read().lower()
    assert "tok-" not in source
    # Normalize whitespace so an irregularly-spaced literal assignment is still caught.
    assert "postmark_server_token =" not in re.sub(r"\s+", " ", source)


def test_runner_has_no_loop_construct():
    # A per-run burst must not hold a session open with a while-loop.
    with open(runner_mod.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert "while " not in source
