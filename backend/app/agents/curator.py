from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.common.normalization import normalize_level
from app.deps import settings
from app.memory.vector_store_pg import retrieve_memory, save_memory
from app.routers import legacy_api


def _llm_client() -> Optional[OpenAI]:
    if not settings.OPENAI_API_KEY:
        return None
    try:
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except Exception:
        return None


def _basic_advice(errors: List[str], level: str, topic_hint: str = "") -> str:
    lvl = normalize_level(level)
    parts: List[str] = []

    if topic_hint:
        parts.append(f"Тема: {topic_hint}")
    if errors:
        parts.append("Типичные ошибки: " + ", ".join(errors))

    tips: List[str] = []
    lowered = [error.lower() for error in errors]

    if any("знак" in item for item in lowered):
        tips.append("Проверяй знаки после каждого преобразования.")
    if any("скоб" in item for item in lowered):
        tips.append("Раскрывай скобки строго по шагам и перепроверяй итог.")
    if any("формул" in item for item in lowered):
        tips.append("Составь короткую памятку формул по теме перед практикой.")

    if lvl == "beginner":
        tips.append("Решай задачу маленькими шагами и фиксируй каждый переход.")
    elif lvl == "intermediate":
        tips.append("Сначала решай без подсказок, затем сверяй с эталоном.")
    else:
        tips.append("Проверяй крайние случаи и формальные допущения решения.")

    if tips:
        parts.append("Советы:\n- " + "\n- ".join(tips))

    return "\n".join(parts) if parts else "Повтори определения и реши 2-3 базовых примера."


def _fallback_profile(*, goals: str, errors: List[str], level: str, note: str) -> Dict[str, Any]:
    normalized_level = normalize_level(level)
    return {
        "level": normalized_level,
        "strengths": [],
        "weaknesses": errors or ["ошибки не указаны"],
        "topics": [goals] if goals else ["основы предмета"],
        "notes": note,
        "advice": _basic_advice(errors, normalized_level, goals),
    }


def _build_prompt(*, goals: str, errors: List[str], level: str, memory_text: str) -> str:
    return (
        "Ты учебный куратор. Проанализируй профиль студента и верни только JSON вида:\n"
        "{\n"
        '  "profile": {\n'
        '    "level": "beginner|intermediate|advanced",\n'
        '    "strengths": ["..."],\n'
        '    "weaknesses": ["..."],\n'
        '    "topics": ["..."],\n'
        '    "notes": "...",\n'
        '    "advice": "..."\n'
        "  }\n"
        "}\n\n"
        f"Память студента:\n{memory_text}\n\n"
        f"Цели: {goals or '—'}\n"
        f"Ошибки: {', '.join(errors) if errors else '—'}\n"
        f"Уровень: {level}\n"
    )


async def assess_student(
    goals: str,
    errors: list[str],
    level: str,
    student_id: str = "default",
) -> dict:
    normalized_level = normalize_level(level)
    clean_errors = [str(error).strip() for error in (errors or []) if str(error).strip()]
    clean_goals = (goals or "").strip()

    try:
        memory_context = retrieve_memory(
            " ".join(clean_errors + [clean_goals]) or "общая тема",
            k=3,
            student_id=student_id,
        )
    except Exception:
        memory_context = []

    memory_text = "\n".join(memory_context) if memory_context else "нет предыдущих данных"

    profile_data: Dict[str, Any]
    client = _llm_client()

    if client is None:
        profile_data = _fallback_profile(
            goals=clean_goals,
            errors=clean_errors,
            level=normalized_level,
            note="OPENAI_API_KEY не задан — использован детерминированный профиль.",
        )
    else:
        prompt = _build_prompt(
            goals=clean_goals,
            errors=clean_errors,
            level=normalized_level,
            memory_text=memory_text,
        )
        try:
            chat = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "Ты опытный учебный куратор. Отвечай строго JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            content = chat.choices[0].message.content or "{}"
            parsed = json.loads(content)
            raw_profile = parsed.get("profile") or {}

            profile_data = {
                "level": normalize_level(str(raw_profile.get("level") or normalized_level)),
                "strengths": [str(x) for x in (raw_profile.get("strengths") or []) if str(x).strip()],
                "weaknesses": [str(x) for x in (raw_profile.get("weaknesses") or clean_errors) if str(x).strip()]
                or ["ошибки не указаны"],
                "topics": [str(x) for x in (raw_profile.get("topics") or ([clean_goals] if clean_goals else [])) if str(x).strip()]
                or ["основы предмета"],
                "notes": str(raw_profile.get("notes") or ""),
                "advice": str(raw_profile.get("advice") or _basic_advice(clean_errors, normalized_level, clean_goals)),
            }
        except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError):
            profile_data = _fallback_profile(
                goals=clean_goals,
                errors=clean_errors,
                level=normalized_level,
                note="LLM недоступен (квота/сеть) — использован детерминированный профиль.",
            )
        except Exception:
            profile_data = _fallback_profile(
                goals=clean_goals,
                errors=clean_errors,
                level=normalized_level,
                note="Не удалось разобрать ответ LLM — использован детерминированный профиль.",
            )

    try:
        save_memory(
            student_id,
            (
                "Куратор оценил ученика.\n"
                f"Цели: {clean_goals or '—'}.\n"
                f"Ошибки: {', '.join(clean_errors) if clean_errors else '—'}.\n"
                f"Уровень: {profile_data.get('level')}.\n"
                f"Совет: {str(profile_data.get('advice') or '')[:200]}"
            ),
            {
                "kind": "curator_assessment",
                "level": profile_data.get("level"),
                "goals": clean_goals,
                "errors": clean_errors,
                "profile": profile_data,
            },
        )
    except Exception:
        pass

    try:
        legacy_api._DB_STUDENT = legacy_api.StudentProfile(
            name="",
            goals=clean_goals,
            level=profile_data.get("level", normalized_level),
            notes=profile_data.get("notes", ""),
        )
    except Exception:
        pass

    return profile_data
