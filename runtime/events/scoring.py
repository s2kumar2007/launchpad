"""
Scoring module for the Gap Filler.

Formula (all weights configurable via env):
    score = a * preference_match + b * wait_time_norm + c * reliability

Where:
    preference_match: fraction of preferences matched (service, staff, time window)
    wait_time_norm:   min(wait_hours / 24, 1)  — capped at 1 after 24 h
    reliability:      accepted / (accepted + no_shows); default 0.8 for new customers

Weights default: a=0.4, b=0.3, c=0.3
"""
from __future__ import annotations

import math
import os
import time
from typing import Any


# ---------------------------------------------------------------------------
# Configurable weight defaults (env: GF_WEIGHT_A, GF_WEIGHT_B, GF_WEIGHT_C)
# ---------------------------------------------------------------------------
def _weights() -> tuple[float, float, float]:
    a = float(os.environ.get("GF_WEIGHT_A", "0.4"))
    b = float(os.environ.get("GF_WEIGHT_B", "0.3"))
    c = float(os.environ.get("GF_WEIGHT_C", "0.3"))
    total = a + b + c
    if total == 0:
        return 0.4, 0.3, 0.3
    return a / total, b / total, c / total   # normalise so they sum to 1


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------
def preference_match(candidate: dict[str, Any], slot: dict[str, Any]) -> float:
    """Return fraction of candidate preferences matched by the freed slot.

    A waitlist entry may carry optional keys:
        preferred_staff   – list[str] of staff_ids
        preferred_times   – list of "HH:MM" time strings
    A slot carries: service_id, staff_id, date, time
    """
    checks: list[bool] = []

    # Service always matches (waitlist is per-service)
    checks.append(True)

    preferred_staff: list[str] = candidate.get("preferred_staff", [])
    if preferred_staff:
        checks.append(slot.get("staff_id") in preferred_staff)

    preferred_times: list[str] = candidate.get("preferred_times", [])
    if preferred_times:
        checks.append(slot.get("time") in preferred_times)

    return sum(checks) / len(checks) if checks else 1.0


def wait_time_norm(joined_ts: float, now_ts: float | None = None) -> float:
    """Return normalised wait time (0..1), capped at 1 after 24 h."""
    if now_ts is None:
        now_ts = time.time()
    hours_waited = max(0.0, (now_ts - joined_ts) / 3600.0)
    return min(hours_waited / 24.0, 1.0)


def reliability_score(history: dict[str, Any]) -> float:
    """Compute reliability from a customer's history dict.

    history may carry: accepted (int), no_shows (int)
    Default (missing keys) → 0.8
    """
    accepted = int(history.get("accepted", 0))
    no_shows = int(history.get("no_shows", 0))
    total = accepted + no_shows
    if total == 0:
        return 0.8  # new customer default
    return accepted / total


def score_candidate(
    candidate: dict[str, Any],
    slot: dict[str, Any],
    history: dict[str, Any] | None = None,
    now_ts: float | None = None,
) -> float:
    """Compute the composite gap-filler score for a waitlisted candidate.

    Args:
        candidate:  waitlist entry {customer, service_id, joined, ...}
        slot:       freed slot    {service_id, staff_id, date, time, price}
        history:    customer history {accepted, no_shows}  (may be None → defaults)
        now_ts:     current Unix timestamp (injectable for testing)

    Returns:
        float in [0, 1]
    """
    a, b, c = _weights()
    pm = preference_match(candidate, slot)
    wt = wait_time_norm(candidate.get("joined", time.time()), now_ts)
    rel = reliability_score(history or {})
    return a * pm + b * wt + c * rel


def rank_candidates(
    candidates: list[dict[str, Any]],
    slot: dict[str, Any],
    histories: dict[str, dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """Rank all candidates by score descending.

    Returns list of (score, candidate) tuples, highest first.
    """
    if now_ts is None:
        now_ts = time.time()
    histories = histories or {}
    scored = [
        (score_candidate(c, slot, histories.get(c["customer"]), now_ts), c)
        for c in candidates
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored
