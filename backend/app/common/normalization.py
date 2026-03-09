from __future__ import annotations

from typing import Any

_VALID_LEVELS = {"beginner", "intermediate", "advanced"}
_LEVEL_HINTS: tuple[tuple[str, str], ...] = (
    ("нач", "beginner"),
    ("сред", "intermediate"),
    ("прод", "advanced"),
)


def normalize_level(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in _VALID_LEVELS:
        return raw

    for marker, normalized in _LEVEL_HINTS:
        if marker in raw:
            return normalized

    return "beginner"


def coerce_str_list(value: Any, *, limit: int | None = None) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if limit is not None and len(result) >= limit:
            break

    return result


def clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed
