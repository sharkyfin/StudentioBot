# app/routers/agents.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import settings
from app.agents import curator, examiner, materials_agent, orchestrator
from app.common.normalization import clamp_int
from app.common.questions import sanitize_questions
from app.memory.vector_store_pg import save_memory



# Опционально используем LLM для извлечения goals/errors из диалога (с фолбэком)
try:
    from openai import OpenAI
    from openai import RateLimitError, AuthenticationError, APIConnectionError, APIStatusError
    _LLM_OK = bool(settings.OPENAI_API_KEY)
    _client = OpenAI(api_key=settings.OPENAI_API_KEY) if _LLM_OK else None
except Exception:
    _LLM_OK = False
    _client = None

router = APIRouter(prefix="/v1/agents", tags=["agents"])


# ====== Pydantic-схемы ======

Role = Literal["system", "user", "assistant"]


class ChatMsg(BaseModel):
    role: Role
    content: str

class PlanStep(BaseModel):
    id: str
    type: Literal["exam", "materials", "chat", "other"]
    title: str
    description: str
    meta: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["prepared", "pending", "error"] = "pending"


class OrchestratorBlock(BaseModel):
    instruction_message: str
    plan_steps: List[PlanStep] = Field(default_factory=list)

    # Какому агенту «передать ход» дальше:
    # - examiner  → страница с тестами
    # - materials → страница с материалами
    # - curator   → остаться в чате куратора
    # - none      → никуда автоматически не идти
    next_agent: Optional[Literal["examiner", "materials", "curator", "none"]] = "none"

    # Какой фронтовый route стоит открыть следующем (если не None)
    # Например: "/tests" или "/materials"
    auto_route: Optional[str] = None

    # ID шага плана, который считается «главным» (первый приоритет)
    primary_step_id: Optional[str] = None



class CuratorFromChatRequest(BaseModel):
    student_id: str = "default"
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    topic: str = ""                      # тема из UI
    messages: List[ChatMsg]              # диалог
    make_exam: bool = False
    count: int = 5


class CuratorFromChatResponse(BaseModel):
    ok: bool
    topic: str
    goals: str
    errors: List[str]
    profile: Dict[str, Any]
    exam: Optional[Dict[str, Any]] = None
    orchestrator: Optional[OrchestratorBlock] = None


class ExaminerReq(BaseModel):
    student_id: str = "default"
    count: int = 5


class ExaminerResp(BaseModel):
    ok: bool
    questions: List[Dict[str, Any]]
    rubric: str


# ====== Утилиты ======

def _save_chat_snapshot(student_id: str, topic: str, messages: list[dict]) -> None:
    """
    Сохраняем кусок диалога (последние N сообщений) в student_memory
    для дальнейшего семантического поиска.
    """
    # оставим последние 20 сообщений, чтобы не раздувать одну запись
    tail = messages[-20:]

    lines: list[str] = []
    for m in tail:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        prefix = {
            "user": "Ученик",
            "assistant": "Куратор",
            "system": "Система",
        }.get(role, role or "unknown")
        lines.append(f"{prefix}: {content}")

    if not lines:
        return

    text = "=== CHAT SNIPPET ===\n" + "\n".join(lines)

    meta = {
        "kind": "chat",
        "topic": topic or "",
        "message_count": len(lines),
    }

    save_memory(student_id, text, meta)


def _safe_student_id(value: str) -> str:
    return (value or "").strip() or "default"


def _serialize_chat_messages(messages: List[ChatMsg], limit: int = 30) -> List[Dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages[-limit:]]


def _heuristic_extract(messages: List[ChatMsg], topic_hint: str) -> tuple[str, List[str]]:
    """
    Простой извлекатель целей/ошибок без LLM:
      - goals = topic_hint (если задан) или краткое резюме из последнего user-сообщения;
      - errors — ищем фразы "не понимаю/ошибка/путаю/трудно/сложно/проблема".
    """
    text_all = "\n".join(m.content for m in messages if m and m.content)
    user_texts = [m.content for m in messages if m.role == "user"]
    last_user = user_texts[-1] if user_texts else ""

    goals = (topic_hint or "").strip()
    if not goals:
        # берём первые 80 символов последнего вопроса пользователя как "цель/тему"
        goals = re.sub(r"\s+", " ", last_user).strip()[:80] or "общая тема"

    # вытягиваем "ошибки" по ключевым словам
    err_keys = ["не понимаю", "не получается", "ошибка", "путаю", "трудно", "сложно", "проблем", "косяк"]
    errors = []
    for k in err_keys:
        if k in text_all.lower():
            errors.append(k)
    # убираем дубли и ограничим разумно
    errors = list(dict.fromkeys(errors))[:6]
    return goals, errors


def _llm_extract(messages: List[ChatMsg], topic_hint: str) -> Optional[tuple[str, List[str]]]:
    """
    Пытаемся извлечь goals/errors через LLM (строгий JSON). При любой ошибке → None.
    """
    if not _LLM_OK or not _client:
        return None
    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "Ты помощник-экстрактор. Верни только JSON вида:\n"
        "{\"goals\":\"...\",\"errors\":[\"...\"]}\n"
        "Без пояснений."
    )
    user = {
        "topic_hint": topic_hint,
        "messages": [{"role": m.role, "content": m.content} for m in messages][-30:],
    }

    try:
        resp = _client.chat.completions.create(
            model=model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        goals = (data.get("goals") or topic_hint or "").strip()
        errors = [e for e in (data.get("errors") or []) if str(e).strip()]
        return goals or "общая тема", errors[:8]
    except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError) as e:
        print(f"[agents.llm_extract] API error: {e}")
        return None
    except Exception as e:
        print(f"[agents.llm_extract] parse error: {e}")
        return None


# ====== РОУТЫ ======

@router.post("/curator/from_chat", response_model=CuratorFromChatResponse)
async def curator_from_chat(req: CuratorFromChatRequest):
    """
    1) Извлекаем цели/ошибки из переписки (LLM → эвристика).
    2) Вызываем Куратора (он подтянет память, применит LLM/эвристику и сохранит срез).
    3) Вызываем оркестратора, который строит план и при необходимости дергает под-агентов.
    4) (Опционально) дополнительно генерим экзамен прямо сейчас, если make_exam=True.
    """
    # шаг 1: goals/errors
    extracted = _llm_extract(req.messages, req.topic) or _heuristic_extract(req.messages, req.topic)
    goals, errors = extracted
    student_id = _safe_student_id(req.student_id)
    safe_count = clamp_int(req.count, default=5, min_value=1, max_value=20)

    # готовим сырые сообщения для оркестратора И для сохранения в память
    chat_messages_for_orchestrator = _serialize_chat_messages(req.messages)

    _save_chat_snapshot(
        student_id=student_id,
        topic=req.topic or goals or "",
        messages=chat_messages_for_orchestrator,
    )

    # шаг 2: оцениваем знания


    # шаг 2: оцениваем знания
    profile = await curator.assess_student(
        goals=goals,
        errors=errors,
        level=req.level,
        student_id=student_id,
    )

    # профайл для оркестратора — добавляем goals внутрь
    profile_for_orchestrator = dict(profile)
    profile_for_orchestrator.setdefault("goals", [goals] if goals else [])

    # шаг 3: строим план и дергаем под-агентов
    orchestrator_block = None
    try:
        orchestrator_block = await orchestrator.plan_and_execute(
            student_id=student_id,
            profile=profile_for_orchestrator,
            chat_messages=chat_messages_for_orchestrator,
        )
    except Exception as e:
        # не ломаем основной ответ из-за ошибок оркестратора
        print(f"[agents.curator_from_chat] orchestrator failed: {e}")
        orchestrator_block = None

    resp: Dict[str, Any] = {
        "ok": True,
        "topic": req.topic or goals,
        "goals": goals,
        "errors": errors,
        "profile": profile,
        "orchestrator": orchestrator_block,
    }

    # шаг 4: при необходимости сразу делаем экзамен и возвращаем его в ответе
    if req.make_exam:
        data = examiner.generate_exam(count=safe_count, student_id=student_id)
        data["questions"] = sanitize_questions(data.get("questions", []))
        resp["exam"] = data

    return resp



@router.post("/examiner", response_model=ExaminerResp)
async def examiner_route(req: ExaminerReq):
    """
    Генерация персональных тестов по последнему "срезу" Куратора.
    Гарантирует заполненные поля (text/options/answer).

    Если оркестратор заранее подготовил экзамен для данного student_id,
    то сначала пытаемся отдать его (и только если его нет — генерируем новый).
    """
    # сначала пробуем взять предгенерированный экзамен
    student_id = _safe_student_id(req.student_id)
    safe_count = clamp_int(req.count, default=5, min_value=1, max_value=20)

    prepared = None
    try:
        prepared = examiner.pop_prepared_exam(student_id)  # type: ignore[attr-defined]
    except Exception as e:
        print(f"[agents.examiner_route] pop_prepared_exam failed: {e}")
        prepared = None

    if prepared is not None:
        data = prepared
    else:
        data = examiner.generate_exam(count=safe_count, student_id=student_id)

    questions = sanitize_questions(data.get("questions", []))
    return {
        "ok": True,
        "questions": questions,
        "rubric": data.get("rubric", "1 балл за верный ответ."),
    }

class AfterExamRequest(BaseModel):
    student_id: str = "default"
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    topic: str = ""
    ok: int          # сколько правильных
    total: int       # сколько всего


class AfterExamResponse(BaseModel):
    ok: bool
    orchestrator: OrchestratorBlock


@router.post("/after_exam", response_model=AfterExamResponse)
async def after_exam(req: AfterExamRequest):
    """
    Вызывается после прохождения теста.
    По результату теста обновляем профиль и снова дергаем Orchestrator,
    чтобы он решил, что делать дальше: материалы, новый тест или куратор.
    """
    student_id = _safe_student_id(req.student_id)
    # очень простой мэппинг результата → "ошибки" для куратора
    ratio = req.ok / max(1, req.total)
    errors: List[str] = []

    if ratio < 0.5:
        errors.append("плохо справился с тестом по теме")
    elif ratio < 0.8:
        errors.append("остались заметные пробелы по теме")
    else:
        errors.append("в целом хорошо справился с тестом")

    goals = req.topic or "закрепить текущую тему"

    # обновляем профиль через Куратора (он сам использует память)
    profile = await curator.assess_student(
        goals=goals,
        errors=errors,
        level=req.level,
        student_id=student_id,
    )

    # снова запускаем оркестратор: пусть он решает, что дальше
    orch_block = await orchestrator.plan_and_execute(
        student_id=student_id,
        profile=profile,
    )

    return AfterExamResponse(ok=True, orchestrator=orch_block)


# ====== ТВОЙ АГЕНТ: МАТЕРИАЛЫ ======
class MaterialsRequest(BaseModel):
    student_id: str = "default"


class MaterialsGenerateResponse(BaseModel):
    ok: bool
    materials: List[Dict[str, Any]]
    meta: Optional[Dict[str, Any]] = None


@router.post("/materials/generate", response_model=MaterialsGenerateResponse)
def generate_materials(req: MaterialsRequest):
    """
    Генерирует/обновляет материалы для студента
    и возвращает ещё meta от MaterialsAgent (комментарий + рекомендации).
    """
    student_id = _safe_student_id(req.student_id)

    # Профиль достаём так же, как внутри materials_agent,
    # чтобы MaterialsAgent понимал темы/слабые места.
    try:
        profile = materials_agent.extract_profile(student_id)
    except Exception as e:
        print(f"[agents.generate_materials] extract_profile failed: {e}")
        profile = {"level": "beginner", "topics": [], "weaknesses": []}

    generated = materials_agent.generate_and_save_materials(student_id)

    # Берём актуальные материалы из БД
    materials = materials_agent.get_materials_for_student(student_id)
    topics = profile.get("topics") or []
    weaknesses = profile.get("weaknesses") or []

    meta: Dict[str, Any] = {
        "status": "ok",
        "comment": (
            f"Материалы обновлены: {len(generated)} новых, всего доступно {len(materials)}."
        ),
        "study_suggestions": [
            "1) Начни с конспекта и восстанови базовую модель темы.",
            "2) Затем пройди шпаргалку и сверяйся с типичными ошибками.",
            "3) После этого переходи к тестам для закрепления.",
        ],
        "focus_topics": topics[:5] if isinstance(topics, list) else [],
        "weaknesses": weaknesses[:5] if isinstance(weaknesses, list) else [],
    }

    return {"ok": True, "materials": materials, "meta": meta}


@router.get("/materials")
def get_materials(student_id: str = "default"):
    """Возвращает материалы для студента."""
    return materials_agent.get_materials_for_student(_safe_student_id(student_id))
