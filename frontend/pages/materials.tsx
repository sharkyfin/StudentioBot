import { useEffect, useState } from 'react';
import { generateMaterials, listMaterials, Material } from '../lib/api';
import { StoredProfile, getStoredProfile } from '../lib/studentProfile';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

export default function MaterialsPage() {
    const [studentId, setStudentId] = useState('default');
    const [profile, setProfile] = useState<StoredProfile | null>(null);

    const [materials, setMaterials] = useState<Material[]>([]);
    const [initialLoading, setInitialLoading] = useState(true);
    const [generating, setGenerating] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // 1) Забираем профиль из localStorage, чтобы знать student_id и последнюю тему
    useEffect(() => {
        const storedProfile = getStoredProfile();
        if (storedProfile?.student_id) {
            setStudentId(storedProfile.student_id);
        }
        setProfile(storedProfile);
    }, []);

    // 2) При изменении studentId подгружаем материалы из backend
    useEffect(() => {
        async function load() {
            setInitialLoading(true);
            setError(null);
            try {
                const data = await listMaterials(studentId);
                setMaterials(data || []);
            } catch (e) {
                console.error(e);
                setError(
                    'Не удалось загрузить материалы. Попробуй сгенерировать новые.'
                );
            } finally {
                setInitialLoading(false);
            }
        }

        load();
    }, [studentId]);

    async function handleGenerate() {
        setGenerating(true);
        setError(null);
        try {
            // 1) просим бэкенд сгенерить материалы + получить meta от MaterialsAgent
            const resp = await generateMaterials(studentId);

            // 2) вытягиваем все материалы этого студента (на всякий случай — как и раньше)
            const all = await listMaterials(studentId);
            setMaterials(all);

            // 3) сохраняем комментарий и рекомендации
            if (resp?.meta) {
                setMaterialsComment(resp.meta.comment || null);
                setStudySuggestions(resp.meta.study_suggestions || []);
            } else {
                setMaterialsComment(null);
                setStudySuggestions([]);
            }
        } catch (e) {
            console.error(e);
            setError('Ошибка при генерации материалов. Попробуй ещё раз.');
        } finally {
            setGenerating(false);
        }
    }

    const hasMaterials = materials && materials.length > 0;
    const notes = materials.filter((m) => m.type === 'notes');
    const cheats = materials.filter((m) => m.type === 'cheat_sheet');
    const links = materials.filter((m) => m.type === 'link');
    const [materialsComment, setMaterialsComment] = useState<string | null>(
        null
    );
    const [studySuggestions, setStudySuggestions] = useState<string[]>([]);

    return (
        <div className="space-y-6">
            <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div>
                    <h1 className="text-2xl font-semibold">Материалы</h1>
                    <p className="text-sm text-white/60">
                        Конспекты, шпаргалки и ссылки, сгенерированные под твой
                        профиль.
                    </p>
                </div>
                <div className="text-xs text-white/50">
                    Student ID:{' '}
                    <span className="font-mono bg-white/5 px-2 py-1 rounded-lg">
                        {studentId || 'default'}
                    </span>
                </div>
            </header>

            {/* Инфо о профиле, которую положил Куратор */}
            {profile && (
                <div className="card p-4 text-sm text-white/70 space-y-1">
                    <div>
                        <span className="text-white/50">Уровень:</span>{' '}
                        <span className="font-medium">
                            {profile.level || 'не указан'}
                        </span>
                    </div>
                    <div>
                        <span className="text-white/50">Последняя тема:</span>{' '}
                        <span className="font-medium">
                            {profile.last_topic || profile.topics?.[0] || '—'}
                        </span>
                    </div>
                    {profile.weaknesses && profile.weaknesses.length > 0 && (
                        <div>
                            <span className="text-white/50">Слабые места:</span>{' '}
                            <span>{profile.weaknesses.join(', ')}</span>
                        </div>
                    )}
                </div>
            )}

            {/* Панель действий */}
            <div className="card p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <div className="text-sm text-white/70">
                    Нажми «Сгенерировать», чтобы агент подобрал материалы под
                    твои ошибки и цели.
                </div>
                <button
                    onClick={handleGenerate}
                    disabled={generating}
                    className="rounded-xl px-4 py-2 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                >
                    {generating ? 'Генерация…' : 'Сгенерировать материалы'}
                </button>
            </div>

            {/* Рекомендации от MaterialsAgent */}
            {(materialsComment || studySuggestions.length > 0) && (
                <div className="card p-4 space-y-2">
                    {materialsComment && (
                        <p className="text-sm text-white/80">
                            {materialsComment}
                        </p>
                    )}
                    {studySuggestions.length > 0 && (
                        <ul className="list-disc pl-5 text-sm text-white/70 space-y-1">
                            {studySuggestions.map((line, idx) => (
                                <li key={idx}>{line}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {/* Список материалов */}
            <div className="space-y-6">
                {initialLoading && (
                    <div className="text-sm text-white/60">
                        Загрузка материалов…
                    </div>
                )}

                {!initialLoading && !hasMaterials && !error && (
                    <div className="card p-4 text-sm text-white/60">
                        Пока материалов нет. Сначала пообщайся с Куратором на
                        главной странице, а потом нажми «Сгенерировать
                        материалы».
                    </div>
                )}

                {hasMaterials && (
                    <>
                        {/* Секция: Конспекты */}
                        {notes.length > 0 && (
                            <section className="space-y-3">
                                <h2 className="text-xl font-semibold">
                                    Конспекты
                                </h2>
                                {notes.map((m, i) => (
                                    <div
                                        key={`notes-${i}`}
                                        className="card p-4 space-y-2"
                                    >
                                        <h3 className="font-semibold text-lg">
                                            {m.title}
                                        </h3>

                                        {m.content && (
                                            <div className="text-sm text-white/70 leading-tight space-y-1">
                                                <ReactMarkdown
                                                    remarkPlugins={[
                                                        remarkGfm,
                                                        remarkMath,
                                                    ]}
                                                    rehypePlugins={[
                                                        rehypeKatex,
                                                    ]}
                                                    components={{
                                                        p: ({
                                                            node,
                                                            ...props
                                                        }) => (
                                                            <p
                                                                className="mb-2"
                                                                {...props}
                                                            />
                                                        ),
                                                        li: ({
                                                            node,
                                                            ...props
                                                        }) => (
                                                            <li
                                                                className="mb-1"
                                                                {...props}
                                                            />
                                                        ),
                                                    }}
                                                >
                                                    {m.content}
                                                </ReactMarkdown>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </section>
                        )}

                        {/* Секция: Шпаргалки */}
                        {cheats.length > 0 && (
                            <section className="space-y-3">
                                <h2 className="text-xl font-semibold mt-6">
                                    Шпаргалки
                                </h2>
                                {cheats.map((m, i) => (
                                    <div
                                        key={`cheat-${i}`}
                                        className="card p-4 space-y-2"
                                    >
                                        <h3 className="font-semibold text-lg">
                                            {m.title}
                                        </h3>

                                        {m.content && (
                                            <div className="text-sm text-white/70 leading-tight space-y-1">
                                                <ReactMarkdown
                                                    remarkPlugins={[
                                                        remarkGfm,
                                                        remarkMath,
                                                    ]}
                                                    rehypePlugins={[
                                                        rehypeKatex,
                                                    ]}
                                                >
                                                    {m.content}
                                                </ReactMarkdown>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </section>
                        )}

                        {/* Секция: Полезные ссылки */}
                        {links.length > 0 && (
                            <section className="space-y-3">
                                <h2 className="text-xl font-semibold mt-6">
                                    Полезные ссылки
                                </h2>
                                {links.map((m, i) => (
                                    <div
                                        key={`link-${i}`}
                                        className="card p-4 space-y-2"
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <h3 className="font-semibold text-lg">
                                                {m.title}
                                            </h3>
                                        </div>

                                        {m.url && (
                                            <a
                                                href={m.url}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200 underline"
                                            >
                                                Открыть ресурс
                                            </a>
                                        )}

                                        {m.content && (
                                            <div className="text-xs text-white/60">
                                                <ReactMarkdown
                                                    remarkPlugins={[remarkGfm]}
                                                >
                                                    {m.content}
                                                </ReactMarkdown>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </section>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
