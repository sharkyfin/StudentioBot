from __future__ import annotations

import json
import re
from typing import Any, Mapping

from app.common.normalization import coerce_str_list, normalize_level

_PROFILE_JSON_RE = re.compile(r"profile:\\s*(\\{.*\\})", re.IGNORECASE | re.DOTALL)


def extract_profile_from_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    default_topic: str = "базовые понятия",
    max_items: int = 5,
) -> dict[str, Any]:
    if not snapshot:
        return {
            "level": "beginner",
            "topics": [default_topic],
            "weaknesses": [],
        }

    topics: list[str] = []
    weaknesses: list[str] = []
    level = "beginner"

    meta = snapshot.get("meta") if isinstance(snapshot, Mapping) else None
    if isinstance(meta, Mapping):
        level = normalize_level(str(meta.get("level") or ""))
        topics = coerce_str_list(meta.get("topics"), limit=max_items)
        weaknesses = coerce_str_list(meta.get("errors"), limit=max_items)

    if not topics or not weaknesses:
        text = str(snapshot.get("text") or "")
        match = _PROFILE_JSON_RE.search(text)
        if match:
            try:
                profile_payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                profile_payload = {}

            if not topics:
                topics = coerce_str_list(profile_payload.get("topics"), limit=max_items)
            if not weaknesses:
                weaknesses = coerce_str_list(
                    profile_payload.get("weaknesses"),
                    limit=max_items,
                )
            level = normalize_level(str(profile_payload.get("level") or level))

    return {
        "level": normalize_level(level),
        "topics": topics or [default_topic],
        "weaknesses": weaknesses,
    }
