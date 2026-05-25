"""Per-run deployment entrypoint (SAT-256 / PRD §Deployment).

The service runs on Claude Managed Agents as a short, per-run burst: one pass
through parse -> build -> send, then exit. There is no held-open session (no
loop), which keeps cost proportional to runs rather than wall-clock time.

Credentials are read from the runtime environment (supplied by Managed Agents'
credential handling or a secrets manager) and never committed. The burst itself
takes an injected pipeline callable, so the same logic runs unchanged on the
self-hosted Claude Agent SDK — only the wiring around it differs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

REQUIRED_ENV = ("POSTMARK_SERVER_TOKEN", "WHITELIST_EMAIL")


@dataclass
class RuntimeConfig:
    """Runtime config resolved from the environment. The token is redacted in repr."""

    postmark_server_token: str
    whitelist_email: str

    def __repr__(self) -> str:
        return (
            "RuntimeConfig(postmark_server_token='***redacted***', "
            f"whitelist_email={self.whitelist_email!r})"
        )


def load_runtime_config(env: Mapping[str, str]) -> RuntimeConfig:
    """Build a RuntimeConfig from ``env``, raising if any required value is missing."""
    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    if missing:
        raise RuntimeError(f"missing required runtime config: {', '.join(missing)}")
    return RuntimeConfig(
        postmark_server_token=env["POSTMARK_SERVER_TOKEN"],
        whitelist_email=env["WHITELIST_EMAIL"],
    )


def run_once(*, run_pipeline: Callable[[], Any]) -> Any:
    """Execute a single per-run burst and return its result. No loop, no held session."""
    return run_pipeline()
