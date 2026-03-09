from __future__ import annotations

from typing import Any, Mapping, Sequence


def sanitize_question(
    question: Mapping[str, Any],
    index: int,
    *,
    options_count: int = 4,
) -> dict[str, Any]:
    text = str(question.get("text") or "").strip() or f"(fallback) Вопрос {index + 1}"

    options = question.get("options") or []
    if not isinstance(options, list):
        options = []

    normalized_options = [str(item).strip() for item in options if str(item).strip()][:options_count]
    while len(normalized_options) < options_count:
        normalized_options.append(f"Вариант {len(normalized_options) + 1}")

    try:
        answer = int(question.get("answer")) if question.get("answer") is not None else 0
    except (TypeError, ValueError):
        answer = 0

    if not (0 <= answer < options_count):
        answer = 0

    return {
        "id": str(question.get("id") or f"q{index + 1}"),
        "text": text,
        "options": normalized_options,
        "answer": answer,
    }


def sanitize_questions(
    questions: Sequence[Mapping[str, Any]] | None,
    *,
    options_count: int = 4,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, question in enumerate(questions or []):
        out.append(sanitize_question(question, idx, options_count=options_count))
    return out
