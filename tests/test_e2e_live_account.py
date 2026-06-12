"""Live account-level end-to-end test (opt-in, real I/O).

Unlike ``test_e2e_serverless.py`` — which exercises the pipeline with a mocked
Postmark and synthetic HTML — this test drives the REAL ``cli.main`` with no
injected seams: real Gmail OAuth read, real EPUB build, real Postmark send to
the real Kindle address configured in ``.env``. It is the "does my actual
account work end to end" check.

Because it reads live Gmail and sends a real email, it is GATED: it only runs
when ``RUN_LIVE_E2E=1`` is set, so the default ``uv run pytest`` suite stays
fully offline.

    RUN_LIVE_E2E=1 uv run pytest tests/test_e2e_live_account.py -s

Optional window override (defaults to the last 7 days):

    RUN_LIVE_E2E=1 LIVE_E2E_START=2026-06-05 LIVE_E2E_END=2026-06-11 \
        uv run pytest tests/test_e2e_live_account.py -s

Credentials come from the process environment, never the repo. A remote agent
(CI, cloud runner) injects the secrets as env vars and points at materialized
credential files:

    POSTMARK_SERVER_TOKEN, WHITELIST_EMAIL, KINDLE_EMAIL  # injected secrets
    GMAIL_BUNDLE_PATH        # dir with client_secret.json + credentials.json
    APPROVED_SOURCES_PATH    # approved senders JSON

Locally, a gitignored ``.env`` overlays the environment for convenience. This
file holds no secrets, so it is safe to commit and run anywhere the credentials
are supplied out of band.

The test injects a throwaway ``state_path`` so the real dedup store
(~/.config/substack-kindle/state.json) is never mutated and already-delivered
newsletters in the window still flow through to a real send. After it passes,
check your Kindle for the digest.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from substack_kindle.cli import main

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_REAL_STATE_PATH = Path("~/.config/substack-kindle/state.json").expanduser()
_REQUIRED_ENV = ("POSTMARK_SERVER_TOKEN", "WHITELIST_EMAIL", "KINDLE_EMAIL")


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. No interpolation, no quoting tricks."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _resolve_env() -> dict[str, str]:
    """Credentials from the process environment (remote agent / CI), with a local
    .env overlay for developer convenience. The repo itself holds no secrets."""
    env = dict(os.environ)
    if _ENV_PATH.exists():
        env.update(_load_dotenv(_ENV_PATH))
    return env


def _default_window() -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=7)
    return start.isoformat(), end.isoformat()


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_E2E") != "1",
    reason="live account e2e is opt-in; set RUN_LIVE_E2E=1 to run (reads Gmail, sends a real email)",
)
def test_live_account_delivers_digest_to_kindle(tmp_path, capsys):
    """Real Gmail → EPUB → Postmark send to the configured Kindle address."""
    env = _resolve_env()
    missing = [k for k in _REQUIRED_ENV if not env.get(k)]
    if missing:
        pytest.skip(f"missing required credentials in env: {', '.join(missing)}")

    start = os.environ.get("LIVE_E2E_START")
    end = os.environ.get("LIVE_E2E_END")
    if not (start and end):
        start, end = _default_window()

    # Throwaway state so the real dedup store is untouched and content in the
    # window is not suppressed as "already delivered".
    state_path = tmp_path / "state.json"
    real_mtime_before = _REAL_STATE_PATH.stat().st_mtime if _REAL_STATE_PATH.exists() else None

    exit_code = main(["--start", start, "--end", end], env=env, state_path=state_path)

    # Surface the run summary line for the human watching with -s.
    print(f"\n[live-e2e] window={start}..{end}\n[live-e2e] {capsys.readouterr().out.strip()}")

    assert exit_code == 0, "live account run did not succeed (exit != 0)"

    # The real dedup store must not have been mutated by this test run.
    if real_mtime_before is not None:
        assert _REAL_STATE_PATH.stat().st_mtime == real_mtime_before, (
            "live e2e must not mutate the real state.json"
        )
