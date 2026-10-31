"""Private-owner authorization helpers for Telegram and local API use."""

from __future__ import annotations

import os


def parse_allowed_users(raw: str | None = None) -> frozenset[int]:
    value = os.getenv("ALLOWED_USERS", "") if raw is None else raw
    users: set[int] = set()
    for item in str(value or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            user_id = int(item)
        except ValueError as exc:
            raise RuntimeError("ALLOWED_USERS must contain only numeric Telegram user IDs.") from exc
        if user_id <= 0:
            raise RuntimeError("ALLOWED_USERS must contain positive Telegram user IDs.")
        users.add(user_id)
    return frozenset(users)


def is_authorized(user_id: int | None, allowed_users: frozenset[int]) -> bool:
    return user_id is not None and user_id in allowed_users
