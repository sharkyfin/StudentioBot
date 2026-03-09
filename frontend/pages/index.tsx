// pages/index.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { API_BASE, curatorFromChat, type ChatMsg } from '../lib/api';
import { saveStoredProfile } from '../lib/studentProfile';

/** ===== Конфиг API (без новых файлов/прокси) =====
 * Укажи во фронтовом .env.local:
 *   NEXT_PUBLIC_API_BASE=https://<твой-backend>.onrender.com
 * Локально можно: http://localhost:10000
 */
const CHAT_ENDPOINT = `${API_BASE}/v1/chat/stream`; // SSE чат прямо на backend

type PlanStepType = 'exam' | 'materials' | 'chat' | 'other';

type PlanStep = {
    id: string;
    type: PlanStepType;
    title: string;
    description: string;
    meta?: Record<string, any>;
    status: 'prepared' | 'pending' | 'error';
};

type OrchestratorBlock = {
    instruction_message: string;
    plan_steps: PlanStep[];
    auto_route?: string; // <- добавили
};

type CuratorFromChatRequest = {
    student_id: string;
    level: 'beginner' | 'intermediate' | 'advanced';
    topic: string;
    messages: ChatMsg[];
    make_exam?: boolean;
    count?: number;
};

type CuratorFromChatResponse = {
    ok: boolean;
    topic: string;
    goals: string;
    errors: string[];
    profile: {
        level: string;
        strengths?: string[];
        weaknesses?: string[];
        topics?: string[];
        notes?: string;
    };
    exam?: any;
    orchestrator?: OrchestratorBlock;
};

/** ===== Вспомогательные функции для SSE ===== */
function parseSSELines(chunk: string): string[] {
    const lines: string[] = [];
    let start = 0;
    while (true) {
        const idx = chunk.indexOf('\n\n', start);
        if (idx === -1) break;
        lines.push(chunk.slice(start, idx));
        start = idx + 2;
    }
    return lines;
}

function normalizeMathDelimiters(content: string): string {
    // Очень грубо, но для чата ок:
    return content
        .replace(/\\\(/g, '$')
        .replace(/\\\)/g, '$')
        .replace(/\\\[/g, '$$')
        .replace(/\\\]/g, '$$');
}

async function* ssePost(url: string, body: any): AsyncGenerator<string> {
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
        },
        body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
        throw new Error(`SSE request failed: ${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = parseSSELines(buffer);
        const lastDoubleNL = buffer.lastIndexOf('\n\n');
        if (lastDoubleNL >= 0) buffer = buffer.slice(lastDoubleNL + 2);

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const payload = line.slice(6);
                if (payload === '[DONE]') return;
                try {
                    const obj = JSON.parse(payload);
                    if (obj.delta) yield obj.delta as string;
                } catch {
                    // игнорируем heartbeat/комменты
                }
            }
        }
    }
}

/** Разбудить Render перед запросом (на бесплатном тарифе он «засыпает») */
async function wakeBackend() {
    try {
        await fetch(`${API_BASE}/health`, { cache: 'no-store' });
    } catch {
        // игнор
    }
}

/** ===== UI главной страницы ===== */
export default function HomePage() {
    const [studentId, setStudentId] = useState('default');
    const [level, setLevel] = useState<
        'beginner' | 'intermediate' | 'advanced'
    >('beginner');
    const [topic, setTopic] = useState('');
    const router = useRouter();
    const [input, setInput] = useState('');
    const [sending, setSending] = useState(false);
    const [evaluating, setEvaluating] = useState(false);

    const [messages, setMessages] = useState<ChatMsg[]>([
        {
            role: 'system',
            content:
                'Ты — Учебный Куратор. Веди диалог, чтобы понять уровень ученика по выбранной теме и его типичные ошибки. Говори кратко и по делу.',
        },
        {
            role: 'assistant',
            content:
                'Привет! Напиши, по какой теме хочешь провериться и что именно вызывает сложности. Я помогу и задам пару уточняющих вопросов.',
        },
    ]);

    // автоскролл чата
    const logRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        logRef.current?.scrollTo({
            top: logRef.current.scrollHeight,
            behavior: 'smooth',
        });
    }, [messages]);

    const canSend = useMemo(
        () => input.trim().length > 0 && !sending && !evaluating,
        [input, sending, evaluating]
    );

    const handleSend = useCallback(async () => {
        const text = input.trim();
        if (!text) return;
        setInput('');
        setSending(true);

        const userMsg: ChatMsg = { role: 'user', content: text };
        const topicContext: ChatMsg = {
            role: 'system',
            content: `Контекст для Куратора: текущая тема = "${
                topic || 'не выбрана'
            }". Отвечай по этой теме.`,
        };

        // История для backend (мы туда topicContext включаем, но в UI его не показываем)
        const history = [...messages, topicContext, userMsg].slice(-20);

        // В UI: добавляем сначала пользователя, потом ПУСТОГО ассистента (которого будем дополнять стримом)
        setMessages((prev) => [
            ...prev,
            userMsg,
            { role: 'assistant', content: '' },
        ]);

        try {
            await wakeBackend();
            for await (const delta of ssePost(CHAT_ENDPOINT, {
                messages: history,
            })) {
                setMessages((prev) => {
                    if (prev.length === 0) return prev;
                    const copy = [...prev];
                    const lastIndex = copy.length - 1;
                    const last = copy[lastIndex];

                    if (last.role !== 'assistant') {
                        // на всякий случай — если что-то пошло не так с порядком
                        return prev;
                    }

                    copy[lastIndex] = {
                        ...last,
                        content: (last.content || '') + delta,
                    };
                    return copy;
                });
            }
        } catch (e) {
            console.error(e);
            setMessages((prev) => {
                if (prev.length === 0) return prev;
                const copy = [...prev];
                const lastIndex = copy.length - 1;
                const last = copy[lastIndex];

                if (last.role !== 'assistant') {
                    return prev;
                }

                copy[lastIndex] = {
                    ...last,
                    content:
                        (last.content || '') +
                        '\n\n[Ошибка сети при получении ответа. Проверь подключение к API (HTTPS) и CORS на backend.]',
                };
                return copy;
            });
        } finally {
            setSending(false);
        }
    }, [input, messages, topic]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            handleSend();
        }
    };

    /** Оценка знаний по конкретной теме: извлекаем goals/errors из диалога и сохраняем профиль */
    const handleEvaluateTopic = useCallback(async () => {
        if (!topic.trim()) {
            alert('Укажи тему, по которой оценивать знания.');
            return;
        }
        setEvaluating(true);
        try {
            const payload: CuratorFromChatRequest = {
                student_id: studentId || 'default',
                level,
                topic,
                messages,
            };

            await wakeBackend();
            const data: CuratorFromChatResponse = await curatorFromChat(payload);

            // Сохраним «срез профиля» в localStorage — его подберут /tests и /materials
            saveStoredProfile({
                student_id: payload.student_id,
                level: data?.profile?.level || level,
                goals: data?.goals || '',
                topics: data?.profile?.topics?.length ? data.profile.topics : [topic],
                weaknesses: data?.profile?.weaknesses || [],
                last_topic: topic,
            });

            // Базовое резюме от куратора
            const baseSummary =
                `Готово! Я оценил твой уровень по теме «${topic}».\n` +
                `Цель: ${data?.goals || '—'}\n` +
                `Слабые места: ${data?.errors?.join(', ') || 'не явные'}\n` +
                `Оценка уровня: ${data?.profile?.level || level}.`;

            // План от оркестратора (главного бота)
            let orchestratorText = '';
            if (data?.orchestrator) {
                const o = data.orchestrator;
                if (o.instruction_message) {
                    orchestratorText +=
                        `\n\nПлан действий от Главного бота:\n` +
                        o.instruction_message;
                }
                if (o.plan_steps && o.plan_steps.length > 0) {
                    const stepsText = o.plan_steps
                        .map(
                            (step, idx) =>
                                `${idx + 1}) ${step.title}: ${step.description}`
                        )
                        .join('\n');
                    orchestratorText += `\n\nШаги плана:\n${stepsText}`;
                }
                if (o.auto_route) {
                    // o.auto_route сейчас приходит из бэка как "/tests" или "/materials"
                    router.push(o.auto_route);
                }
                // <<< НОВОЕ
            } else {
                // Фолбэк, если по какой-то причине оркестратор не сработал
                orchestratorText +=
                    '\n\nДальше можешь перейти на вкладки «Тесты» и «Материалы», чтобы потренироваться и закрыть пробелы.';
            }

            // Сообщение для пользователя в чате куратора
            setMessages((prev) => [
                ...prev,
                {
                    role: 'assistant',
                    content: baseSummary + orchestratorText,
                },
            ]);
        } catch (e) {
            console.error(e);
            alert(
                'Не удалось провести оценку. Проверь NEXT_PUBLIC_API_BASE, CORS (ALLOWED_ORIGINS) и доступность backend /health.'
            );
        } finally {
            setEvaluating(false);
        }
    }, [studentId, level, topic, messages, router]);

    return (
        <div className="mx-auto max-w-4xl px-4 py-6 space-y-6">
            <header className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">
                    Учебный помощник — Куратор
                </h1>
                <div className="text-sm text-white/60">
                    Чат → Оценка по теме → Тесты
                </div>
            </header>

            {/* Панель параметров */}
            <div className="grid gap-3 sm:grid-cols-4">
                <input
                    className="rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                    placeholder="Student ID"
                    value={studentId}
                    onChange={(e) => setStudentId(e.target.value)}
                />
                <select
                    className="rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                    value={level}
                    onChange={(e) =>
                        setLevel(
                            e.target.value as
                                | 'beginner'
                                | 'intermediate'
                                | 'advanced'
                        )
                    }
                >
                    <option value="beginner">Новичок</option>
                    <option value="intermediate">Средний</option>
                    <option value="advanced">Продвинутый</option>
                </select>
                <input
                    className="rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15 sm:col-span-2"
                    placeholder="Тема (например, пределы и ε-δ)"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                />
            </div>

            {/* Окно чата */}
            <div
                ref={logRef}
                className="rounded-2xl border border-white/10 bg-white/5 p-4 h-[52vh] overflow-y-auto space-y-4"
            >
                {messages.map((m, i) => (
                    <div key={i} className="flex gap-3">
                        <div
                            className={`h-6 w-6 flex items-center justify-center rounded-full text-xs ${
                                m.role === 'user'
                                    ? 'bg-emerald-500/20'
                                    : m.role === 'assistant'
                                    ? 'bg-sky-500/20'
                                    : 'bg-white/10'
                            }`}
                            title={m.role}
                        >
                            {m.role === 'user'
                                ? 'U'
                                : m.role === 'assistant'
                                ? 'A'
                                : 'S'}
                        </div>
                        <div className="prose prose-invert max-w-none leading-relaxed">
                            <ReactMarkdown
                                remarkPlugins={[remarkGfm, remarkMath]}
                                rehypePlugins={[rehypeKatex]}
                            >
                                {normalizeMathDelimiters(m.content)}
                            </ReactMarkdown>
                        </div>
                    </div>
                ))}
            </div>

            {/* Ввод и кнопки */}
            <div className="flex flex-col sm:flex-row gap-3">
                <input
                    className="flex-1 rounded-xl bg-white/10 px-4 py-3 outline-none focus:ring-2 focus:ring-white/15"
                    placeholder="Напиши сообщение... (Ctrl/⌘+Enter — отправить)"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                />
                <div className="flex gap-3">
                    <button
                        onClick={handleSend}
                        disabled={!canSend}
                        className="rounded-xl px-4 py-3 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                        title="Отправить (Ctrl/⌘+Enter)"
                    >
                        Отправить
                    </button>
                    <button
                        onClick={handleEvaluateTopic}
                        disabled={evaluating || sending}
                        className="rounded-xl px-4 py-3 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                        title="Оценить знания по теме и получить персональный план"
                    >
                        {evaluating ? 'Оцениваем…' : 'Оценить по теме → План'}
                    </button>
                </div>
            </div>

            <footer className="text-xs text-white/50">
                Подсказка: сначала пообщайся с Куратором по выбранной теме,
                затем нажми «Оценить по теме». Главный бот построит план, а
                страницы «Тесты» и «Материалы» будут использовать сохранённый
                профиль.
            </footer>
        </div>
    );
}
