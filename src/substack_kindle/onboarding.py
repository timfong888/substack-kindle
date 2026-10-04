"""Customer onboarding flow (SAT-253 / Reqs 9, §Onboarding).

Walks a new customer through: enter kindle_email -> see the shared
whitelist_email with instructions and the Amazon discovery path. Steps are
gated so they happen in order.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

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
    ENTER_KINDLE_EMAIL = "enter_kindle_email"
    SHOW_WHITELIST = "show_whitelist"
    DONE = "done"


class OnboardingFlow:
    """Stateful, ordered onboarding walkthrough for one new customer."""

    def __init__(self, *, whitelist_email: str) -> None:
        self.whitelist_email = whitelist_email
        self.kindle_email = None
        self.whitelist_shown = False

    def next_step(self) -> OnboardingStep:
        if self.kindle_email is None:
            return OnboardingStep.ENTER_KINDLE_EMAIL
        if not self.whitelist_shown:
            return OnboardingStep.SHOW_WHITELIST
        return OnboardingStep.DONE

    def set_kindle_email(self, kindle_email: str) -> None:
        if not kindle_email or not kindle_email.strip():
            raise ValueError("kindle_email must not be empty")
        # Store the stripped form so downstream delivery/address matching never
        # sees surrounding whitespace from the onboarding input.
        self.kindle_email = kindle_email.strip()

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

    def is_complete(self) -> bool:
        return self.next_step() is OnboardingStep.DONE
