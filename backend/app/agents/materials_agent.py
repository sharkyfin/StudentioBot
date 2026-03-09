from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.common.profile_snapshot import extract_profile_from_snapshot
from app.deps import settings
from app.memory.vector_store_pg import (
    get_conn,
    get_last_curator_snapshot,
    retrieve_memory,
)

_VALID_MATERIAL_TYPES = {"link", "notes", "cheat_sheet"}


def init_materials_table() -> None:
    """Создаёт таблицу materials при запуске (если не существует)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS materials (
                    id SERIAL PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('link', 'notes', 'cheat_sheet')),
                    url TEXT,
                    content TEXT
                );
                """
            )


def extract_profile(student_id: str) -> Dict[str, Any]:
    snap = get_last_curator_snapshot(student_id)
    return extract_profile_from_snapshot(snap)


def _llm_client() -> Optional[OpenAI]:
    if not settings.OPENAI_API_KEY:
        return None
    try:
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except Exception:
        return None


def _build_search_url(platform: str, query: str) -> str:
    q = quote_plus((query or "").strip() or "обучающий материал")
    platform = (platform or "youtube").lower()

    if platform == "rutube":
        return f"https://rutube.ru/search/?q={q}"
    if platform == "youtube":
        return f"https://www.youtube.com/results?search_query={q}"
    return f"https://www.google.com/search?q={q}"


def _material_key(material: Dict[str, Any]) -> str:
    title = (material.get("title") or "").strip()
    material_type = (material.get("type") or "").strip()
    url = (material.get("url") or "").strip()
    content = (material.get("content") or "").strip()
    return f"{title}||{material_type}||{url}||{content}"


def _normalize_material(raw: Dict[str, Any], *, default_topic: str) -> Dict[str, Any]:
    material_type = str(raw.get("type") or "notes").strip().lower()
    if material_type not in _VALID_MATERIAL_TYPES:
        material_type = "notes"

    title = str(raw.get("title") or "Без названия").strip()[:120]

    if material_type == "link":
        platform = str(raw.get("platform") or "youtube").strip().lower()
        query = str(raw.get("query") or default_topic).strip() or default_topic
        return {
            "title": title,
            "type": "link",
            "url": _build_search_url(platform, query),
            "content": None,
        }

    content = str(raw.get("content") or "").strip()
    if not content:
        content = (
            f"Краткий материал по теме: {default_topic}.\n"
            "Сфокусируйся на ключевых шагах решения и самопроверке после каждого примера."
        )

    return {
        "title": title,
        "type": material_type,
        "url": None,
        "content": content,
    }


def _sanitize_materials(raw_items: List[Dict[str, Any]], *, topics: List[str]) -> List[Dict[str, Any]]:
    default_topic = topics[0] if topics else "общая подготовка"
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        material = _normalize_material(raw, default_topic=default_topic)
        key = _material_key(material)
        if key in seen:
            continue
        seen.add(key)
        result.append(material)

    return result


def _fallback_materials(topics: List[str], weaknesses: List[str]) -> List[Dict[str, Any]]:
    main_topic = topics[0] if topics else "общая подготовка"
    weak_points = weaknesses[:5]

    notes_content = (
        f"# Конспект по теме: {main_topic}\n\n"
        "## Что важно понять\n"
        "- Что является входом задачи\n"
        "- Какой результат требуется получить\n"
        "- Какие правила применяются на каждом шаге\n\n"
        "## Мини-практика\n"
        "1. Реши 2 простых примера.\n"
        "2. Реши 2 примера среднего уровня.\n"
        "3. После каждого примера зафиксируй ошибку и исправление.\n"
    )

    if weak_points:
        notes_content += "\n## Точки внимания\n" + "\n".join(
            f"- {point}" for point in weak_points
        )

    cheat_sheet = (
        f"# Шпаргалка: {main_topic}\n\n"
        "## Алгоритм\n"
        "1. Прочитай условие и выдели ключевые данные.\n"
        "2. Выбери правило/формулу.\n"
        "3. Реши по шагам и проверь результат.\n\n"
        "## Проверка\n"
        "- Проверены ли знаки и границы?\n"
        "- Совпадает ли ответ с исходным условием?\n"
    )

    return [
        {
            "title": f"Конспект: {main_topic}",
            "type": "notes",
            "url": None,
            "content": notes_content,
        },
        {
            "title": f"Шпаргалка: {main_topic}",
            "type": "cheat_sheet",
            "url": None,
            "content": cheat_sheet,
        },
        {
            "title": f"Видеоразборы по теме «{main_topic}» (YouTube)",
            "type": "link",
            "url": _build_search_url("youtube", main_topic),
            "content": None,
        },
        {
            "title": f"Практика по теме «{main_topic}» (RuTube)",
            "type": "link",
            "url": _build_search_url("rutube", main_topic),
            "content": None,
        },
    ]


def _generate_materials_with_llm(
    *,
    topics: List[str],
    weaknesses: List[str],
    memory_texts: List[str],
) -> List[Dict[str, Any]]:
    client = _llm_client()
    if client is None:
        return _fallback_materials(topics, weaknesses)

    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    payload = {
        "topics": topics,
        "weaknesses": weaknesses,
        "memory": memory_texts,
        "constraints": {
            "materials_count": "4-6",
            "required_types": ["notes", "cheat_sheet", "link"],
            "link_count": "2-3",
            "language": "ru",
        },
    }

    system_prompt = (
        "Ты методист. Верни только JSON-объект вида "
        '{"materials": [{"title":"...","type":"notes|cheat_sheet|link",'
        '"content":"...|null","platform":"youtube|rutube|other|null","query":"...|null"}]}.'
    )

    user_prompt = (
        "Сгенерируй практичные материалы для студента по входным данным. "
        "Не добавляй текст вне JSON."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        raw_items = data.get("materials") or []
        sanitized = _sanitize_materials(raw_items, topics=topics)
        return sanitized or _fallback_materials(topics, weaknesses)

    except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError):
        return _fallback_materials(topics, weaknesses)
    except Exception:
        return _fallback_materials(topics, weaknesses)


def _save_materials_to_db(student_id: str, materials: List[Dict[str, Any]]) -> None:
    if not materials:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, type, url, content
                FROM materials
                WHERE student_id = %s
                """,
                (student_id,),
            )
            existing_keys: set[str] = {
                _material_key(
                    {
                        "title": title,
                        "type": material_type,
                        "url": url,
                        "content": content,
                    }
                )
                for title, material_type, url, content in cur.fetchall()
            }

            for material in materials:
                key = _material_key(material)
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                cur.execute(
                    """
                    INSERT INTO materials (student_id, title, type, url, content)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        student_id,
                        material["title"],
                        material["type"],
                        material["url"],
                        material["content"],
                    ),
                )


def generate_and_save_materials(student_id: str = "default") -> List[Dict[str, Any]]:
    profile = extract_profile(student_id)
    topics = profile.get("topics") or []
    weaknesses = profile.get("weaknesses") or []

    query_parts: List[str] = []
    query_parts.extend(topics)
    query_parts.extend(weaknesses)
    memory_query = " ".join(query_parts).strip() or "типичные ошибки и вопросы ученика"

    try:
        memory_texts = retrieve_memory(memory_query, k=5, student_id=student_id)
    except Exception:
        memory_texts = []

    materials = _generate_materials_with_llm(
        topics=topics,
        weaknesses=weaknesses,
        memory_texts=memory_texts,
    )

    _save_materials_to_db(student_id, materials)
    return materials


def get_materials_for_student(student_id: str = "default") -> List[Dict[str, Any]]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT title, type, url, content
                    FROM materials
                    WHERE student_id = %s
                    ORDER BY id
                    """,
                    (student_id,),
                )
                rows = cur.fetchall()

        return [
            {
                "title": row[0],
                "type": row[1],
                "url": row[2],
                "content": row[3],
            }
            for row in rows
        ]
    except Exception:
        return []
