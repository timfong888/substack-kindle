"""Within-job deduplication (SAT-249 / Req 17).

A job never re-delivers a newsletter that has already been delivered. ``deduplicate``
drops candidates whose ID is already in the processed-state store (A3) and collapses
duplicates within the same batch. The delivered-check is injected (``is_delivered``)
so this layer stays decoupled from the A3 store; the behavior is identical for every
trigger type (scheduled or backfill) because it depends only on the store and the
candidate IDs, never the trigger.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def deduplicate(
    items: Iterable[T],
    is_delivered: Callable[[str], bool],
    *,
    key: Callable[[T], str] = lambda item: item.newsletter_id,
) -> list[T]:
    """Return items, in order, that are neither already delivered nor batch duplicates."""
    seen: set[str] = set()
    result: list[T] = []
    for item in items:
        newsletter_id = key(item)
        if newsletter_id in seen or is_delivered(newsletter_id):
            continue
        seen.add(newsletter_id)
        result.append(item)
    return result
