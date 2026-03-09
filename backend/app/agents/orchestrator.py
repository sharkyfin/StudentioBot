from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents import examiner, materials_agent
from app.common.normalization import coerce_str_list, normalize_level


def _build_steps(
    *,
    student_id: str,
    level: str,
    topics: List[str],
    weaknesses: List[str],
    goals: List[str],
    primary_type: str,
) -> List[Dict[str, Any]]:
    topic_label = ", ".join(topics[:2]) if topics else "текущая тема"
    weak_label = ", ".join(weaknesses[:2]) if weaknesses else "базовое закрепление"
    goals_label = ", ".join(goals[:2]) if goals else "повышение уверенности в теме"

    materials_step = {
        "id": "step_materials",
        "type": "materials",
        "title": "Разобрать персональные материалы",
        "description": (
            f"Открой раздел «Материалы» и пройди конспект по теме {topic_label}. "
            f"Фокус: {weak_label}."
        ),
        "meta": {
            "student_id": student_id,
            "level": level,
            "topics": topics,
            "weaknesses": weaknesses,
            "goals": goals,
        },
        "status": "pending",
    }

    exam_step = {
        "id": "step_exam",
        "type": "exam",
        "title": "Пройти тренировочный тест",
        "description": (
            f"Перейди во вкладку «Тесты» и реши набор задач по теме {topic_label}. "
            f"Цель: {goals_label}."
        ),
        "meta": {
            "student_id": student_id,
            "level": level,
            "topics": topics,
            "weaknesses": weaknesses,
            "goals": goals,
        },
        "status": "pending",
    }

    chat_step = {
        "id": "step_chat",
        "type": "chat",
        "title": "Обсудить результаты с куратором",
        "description": "После выполнения шага вернись в чат и запроси корректировку плана.",
        "meta": {"student_id": student_id},
        "status": "pending",
    }

    if primary_type == "exam":
        return [exam_step, materials_step, chat_step]
    return [materials_step, exam_step, chat_step]


def _detect_primary_type(
    *,
    level: str,
    weaknesses: List[str],
    chat_messages: Optional[List[Dict[str, str]]] = None,
) -> str:
    joined_chat = "\n".join(str(m.get("content") or "") for m in (chat_messages or [])).lower()

    materials_signals = ("материал", "объясн", "теори", "конспект")
    exam_signals = ("тест", "экзам", "провер", "quiz")

    if any(token in joined_chat for token in materials_signals):
        return "materials"
    if any(token in joined_chat for token in exam_signals):
        return "exam"

    if level == "advanced" and len(weaknesses) <= 1:
        return "exam"

    return "materials"


def _prepare_step(step: Dict[str, Any], *, student_id: str, level: str) -> None:
    step_type = step.get("type")

    if step_type == "materials":
        existing = materials_agent.get_materials_for_student(student_id)
        if not existing:
            materials_agent.generate_and_save_materials(student_id)
            existing = materials_agent.get_materials_for_student(student_id)

        step.setdefault("meta", {})["materials_count"] = len(existing)
        step["status"] = "prepared" if existing else "error"
        return

    if step_type == "exam":
        question_count = 4 if level == "beginner" else 6 if level == "intermediate" else 8
        exam_data = examiner.generate_exam(count=question_count, student_id=student_id)
        examiner.set_prepared_exam(student_id, exam_data)

        questions = exam_data.get("questions") or []
        step.setdefault("meta", {})["questions_prepared"] = len(questions)
        step.setdefault("meta", {})["rubric"] = exam_data.get("rubric")
        step["status"] = "prepared" if questions else "error"
        return

    step["status"] = "pending"


def _route_for_step(step_type: str) -> tuple[str, Optional[str]]:
    if step_type == "materials":
        return "materials", "/materials"
    if step_type == "exam":
        return "examiner", "/tests"
    if step_type == "chat":
        return "curator", None
    return "none", None


async def plan_and_execute(
    student_id: str,
    profile: Dict[str, Any],
    chat_messages: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    level = normalize_level(str(profile.get("level") or "beginner"))
    topics = coerce_str_list(profile.get("topics"), limit=5)
    weaknesses = coerce_str_list(profile.get("weaknesses"), limit=5)
    goals = coerce_str_list(
        profile.get("goals") or profile.get("target") or profile.get("targets"),
        limit=3,
    )

    primary_type = _detect_primary_type(
        level=level,
        weaknesses=weaknesses,
        chat_messages=chat_messages,
    )

    steps = _build_steps(
        student_id=student_id,
        level=level,
        topics=topics,
        weaknesses=weaknesses,
        goals=goals,
        primary_type=primary_type,
    )

    primary_step = steps[0]
    primary_step_id = str(primary_step.get("id"))

    try:
        _prepare_step(primary_step, student_id=student_id, level=level)
    except Exception as exc:
        primary_step["status"] = "error"
        primary_step.setdefault("meta", {})["error"] = str(exc)

    next_agent, auto_route = _route_for_step(str(primary_step.get("type") or "other"))
    if primary_step.get("status") == "error":
        next_agent = "curator"
        auto_route = None

    instruction = (
        "Я собрал персональный план: выполни первый шаг, затем закрепи результат "
        "во втором шаге и вернись к куратору для корректировки траектории."
    )

    return {
        "instruction_message": instruction,
        "plan_steps": steps,
        "next_agent": next_agent,
        "auto_route": auto_route,
        "primary_step_id": primary_step_id,
    }
