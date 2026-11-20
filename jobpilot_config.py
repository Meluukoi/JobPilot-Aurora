"""Validated, read-only JobPilot policy configuration."""

import json
from pathlib import Path

DEFAULTS = {
    "publication_freshness_hours": 336,
    "greenhouse_verification_requests": 10,
    "greenhouse_recovery_days": 14,
    "minimum_relevance_score": 45,
    "discovery_window_days": 45,
    "email_batch_size": 20,
}

LIMITS = {
    "publication_freshness_hours": (1, 336),
    "greenhouse_verification_requests": (0, 10),
    "greenhouse_recovery_days": (1, 90),
    "minimum_relevance_score": (0, 100),
    "discovery_window_days": (1, 365),
    "email_batch_size": (1, 20),
}


def validate_policy(values):
    """Reject unknown settings, unsafe limits, and invalid types."""
    if not isinstance(values, dict):
        raise ValueError("Policy must be a JSON object")

    unknown = set(values) - set(DEFAULTS)

    if unknown:
        raise ValueError(
            f"Unknown policy settings: {sorted(unknown)}"
        )

    policy = dict(DEFAULTS)
    policy.update(values)

    for name, value in policy.items():
        minimum, maximum = LIMITS[name]

        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")

        if not minimum <= value <= maximum:
            raise ValueError(
                f"{name} must be between {minimum} and {maximum}"
            )

    return policy


def load_policy(root):
    """Read policy without modifying files or creating directories."""
    path = Path(root) / "jobpilot_policy.json"

    if not path.exists():
        return validate_policy({})

    with path.open(encoding="utf-8-sig") as file:
        values = json.load(file)

    return validate_policy(values)
