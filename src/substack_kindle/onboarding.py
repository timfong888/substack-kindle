"""Customer onboarding flow (SAT-253 / Reqs 9, §Onboarding).

Walks a new customer through: connect read-only Gmail -> enter kindle_email ->
see the shared whitelist_email with instructions and the Amazon discovery path ->
seed approved_sources by labelling existing newsletters (the B2 gesture). Steps
are gated so they happen in order. External work (Gmail connect, sender
registration) is injected, so this orchestrator stays decoupled.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_AMAZON_DISCOVERY_PATH = (
    "In Amazon: Account & Lists -> Content & Devices -> Preferences -> "
    "Personal Document Settings -> Approved Personal Document E-mail List -> "
    "Add a new approved e-mail address."
)


@dataclass
class WhitelistInstructions:
    whitelist_email: str
    instructions: str
    amazon_discovery_path: str


class OnboardingStep(enum.Enum):
    CONNECT_GMAIL = "connect_gmail"
    ENTER_KINDLE_EMAIL = "enter_kindle_email"
    SHOW_WHITELIST = "show_whitelist"
    SEED_SOURCES = "seed_sources"
    DONE = "done"


class OnboardingFlow:
    """Stateful, ordered onboarding walkthrough for one new customer."""

    def __init__(
        self,
        *,
        whitelist_email: str,
        connect_gmail: Callable[[], Any],
        register_senders: Callable[[str], list[str]],
    ) -> None:
        self.whitelist_email = whitelist_email
        self.connect_gmail_fn = connect_gmail
        self.register_senders = register_senders
        self.gmail_client = None
        self.gmail_connected = False
        self.kindle_email = None
        self.whitelist_shown = False
        self.approved_sources = []
        self.sources_seeded = False

    def next_step(self) -> OnboardingStep:
        if not self.gmail_connected:
            return OnboardingStep.CONNECT_GMAIL
        if self.kindle_email is None:
            return OnboardingStep.ENTER_KINDLE_EMAIL
        if not self.whitelist_shown:
            return OnboardingStep.SHOW_WHITELIST
        if not self.sources_seeded:
            return OnboardingStep.SEED_SOURCES
        return OnboardingStep.DONE

    def connect_gmail(self) -> Any:
        self.gmail_client = self.connect_gmail_fn()
        self.gmail_connected = True
        return self.gmail_client

    def set_kindle_email(self, kindle_email: str) -> None:
        if not self.gmail_connected:
            raise ValueError("connect Gmail before entering the Kindle email")
        self.kindle_email = kindle_email

    def whitelist_instructions(self) -> WhitelistInstructions:
        if self.kindle_email is None:
            raise ValueError("enter the Kindle email before showing whitelist instructions")
        self.whitelist_shown = True
        return WhitelistInstructions(
            whitelist_email=self.whitelist_email,
            instructions=(
                f"Add {self.whitelist_email} to your Kindle's approved sender list so "
                "deliveries are accepted."
            ),
            amazon_discovery_path=_AMAZON_DISCOVERY_PATH,
        )

    def seed_sources(self, *, label: str) -> list[str]:
        if not self.whitelist_shown:
            raise ValueError("show the whitelist instructions before seeding sources")
        self.approved_sources = self.register_senders(label)
        self.sources_seeded = True
        return self.approved_sources

    def is_complete(self) -> bool:
        return self.next_step() is OnboardingStep.DONE
