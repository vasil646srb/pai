"""Генератор синтетического диалога для нагрузочного/качественного теста
памяти pai: сжатие (RAPTOR-like tree) + полнотекстовый поиск (search_archive).

Идея: сгенерировать десятки разрозненных пользовательских реплик о семье —
дни рождения, текущий возраст каждого, кто где работает, какой сейчас год —
разбросанных по диалогу вперемешку с посторонним "шумом" (бытовые фразы),
так же как это выглядело бы в реальном долгом общении. Затем прогнать этот
поток через Orchestrator, чтобы линия нарастания уровней сжатия дерева стала
видна, а потом проверить: остаются ли факты находимыми через search_archive
и через сам чат, после того как правда о них "утонула" в сотнях узлов и
частично свёрнута в саммари верхних уровней.

Никакого обращения к настоящей LLM здесь нет — это чистый генератор текста
(f-строки по шаблонам с случайной подстановкой). Он используется двумя
способами:
  1. get_dialog_lines() -> list[str]            — просто тексты по порядку
  2. get_dialog_events() -> list[DialogEvent]    — тексты + факт, который они
     кодируют (для последующей автоматической проверки, что факт нашёлся)

Всё детерминировано через random.Random(seed), так что прогон воспроизводим.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional


CURRENT_YEAR = 2026  # "какой сейчас год" — намеренно зашито в текст диалога,
                      # а не берётся из datetime.now(), чтобы факт был
                      # проверяемым фактом ИЗ ПАМЯТИ, а не побочным продуктом
                      # системного времени.


@dataclass
class FamilyMember:
    key: str            # стабильный идентификатор для тестов, напр. "mother"
    name: str
    relation: str        # "мама", "папа", "брат", ...
    birth_year: int
    birthday_md: str     # "14 марта"
    job: str
    city: str


# ---------------------------------------------------------------------------
# Синтетическая семья пользователя. Возраст всегда выводится как
# CURRENT_YEAR - birth_year, поэтому "сколько лет" — вычислимый факт, а не
# просто хранимое число (ассистент должен либо запомнить прямую фразу с
# возрастом, либо посчитать его от года рождения + текущего года — оба пути
# теста намеренно встречаются в диалоге).
# ---------------------------------------------------------------------------
FAMILY: list[FamilyMember] = [
    FamilyMember("user", "Игорь", "я", 1994, "3 июня", "backend-разработчик в Sber Tech", "Москва"),
    FamilyMember("mother", "Марина Викторовна", "мама", 1968, "14 марта", "учитель физики в школе №12", "Тверь"),
    FamilyMember("father", "Олег Петрович", "папа", 1965, "2 сентября", "на пенсии, раньше инженер на заводе", "Тверь"),
    FamilyMember("brother", "Дмитрий", "брат", 1997, "21 ноября", "менеджер по продажам в Wildberries", "Москва"),
    FamilyMember("sister", "Алина", "сестра", 2001, "9 июля", "студентка, учится на архитектора", "Санкт-Петербург"),
    FamilyMember("wife", "Настя", "жена", 1996, "27 января", "врач-педиатр в частной клинике", "Москва"),
    FamilyMember("son", "Тимофей", "сын", 2022, "5 декабря", "ещё маленький, в садик пока не ходит", "Москва"),
    FamilyMember("grandmother", "Валентина Ивановна", "бабушка (мамина мама)", 1943, "30 апреля", "на пенсии", "Тверь"),
]


def age_of(member: FamilyMember, year: int = CURRENT_YEAR) -> int:
    return year - member.birth_year


@dataclass
class DialogEvent:
    text: str
    fact_key: Optional[str] = None   # напр. "brother.birthday", "user.year"
    fact_value: Optional[str] = None # ожидаемое значение для последующей проверки
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Шаблоны фраз. Несколько вариантов на каждый тип факта — чтобы диалог не
# выглядел как машинный дамп полей, а как то, как человек естественно
# упоминает такие вещи в разных репликах на протяжении месяцев.
# ---------------------------------------------------------------------------

RELATION_GENITIVE = {
    "мама": "мамы",
    "папа": "папы",
    "брат": "брата",
    "сестра": "сестры",
    "жена": "жены",
    "сын": "сына",
    "бабушка (мамина мама)": "бабушки (маминой мамы)",
}


def _genitive(relation: str) -> str:
    return RELATION_GENITIVE.get(relation, relation)


def _birthday_lines(m: FamilyMember, rnd: random.Random) -> list[DialogEvent]:
    if m.key == "user":
        templates = [
            f"Кстати, у меня самого день рождения {m.birthday_md}, в этом году будет {age_of(m)}.",
            f"Мой ДР — {m.birthday_md}. Хочу в этом году отметить в кругу семьи.",
        ]
    else:
        templates = [
            f"У {_genitive(m.relation)} день рождения {m.birthday_md}, надо не забыть подарок в этом году.",
            f"Не забыть: {m.name} ({m.relation}) празднует ДР {m.birthday_md}.",
            f"{m.birthday_md} у {_genitive(m.relation)} день рождения, в этом году будет {age_of(m)} лет.",
        ]
    text = rnd.choice(templates)
    return [DialogEvent(text=text, fact_key=f"{m.key}.birthday", fact_value=m.birthday_md, tags=["birthday", m.key])]


FEMALE_RELATIONS = {"мама", "сестра", "жена", "бабушка (мамина мама)"}


def _born_verb(m: FamilyMember) -> str:
    return "родилась" if m.relation in FEMALE_RELATIONS else "родился"


def _age_lines(m: FamilyMember, rnd: random.Random) -> list[DialogEvent]:
    age = age_of(m)
    if m.key == "user":
        templates = [
            f"Мне в этом {CURRENT_YEAR} году исполнилось {age} лет.",
            f"Сейчас мне {age} — родился в {m.birth_year}.",
        ]
    else:
        templates = [
            f"{m.relation.capitalize()} {m.name} сейчас {age} лет, {_born_verb(m)} в {m.birth_year} году.",
            f"Забавно, {m.relation} в этом году исполняется {age} — время летит.",
            f"{m.name}, {m.relation}, — {age} лет ({m.birth_year} г.р.).",
        ]
    text = rnd.choice(templates)
    return [DialogEvent(text=text, fact_key=f"{m.key}.age", fact_value=str(age), tags=["age", m.key])]


def _job_lines(m: FamilyMember, rnd: random.Random) -> list[DialogEvent]:
    if m.key == "user":
        templates = [
            f"Я сейчас работаю: {m.job}, в городе {m.city}.",
            f"На работе у меня должность — {m.job}.",
        ]
    else:
        templates = [
            f"{m.name} ({m.relation}) работает: {m.job}. Живёт в {m.city}.",
            f"Кстати, {m.relation} сменил(а) работу — теперь это \"{m.job}\".",
            f"{m.relation.capitalize()} {m.name} трудится как {m.job}, в {m.city}.",
        ]
    text = rnd.choice(templates)
    return [DialogEvent(text=text, fact_key=f"{m.key}.job", fact_value=m.job, tags=["job", m.key])]


def _year_lines(rnd: random.Random) -> list[DialogEvent]:
    templates = [
        f"Напомню на будущее: сейчас у нас {CURRENT_YEAR} год.",
        f"Мы сейчас в {CURRENT_YEAR} году, если вдруг понадобится для расчётов.",
        f"На дворе {CURRENT_YEAR}-й — просто чтобы зафиксировать в контексте.",
    ]
    text = rnd.choice(templates)
    return [DialogEvent(text=text, fact_key="meta.year", fact_value=str(CURRENT_YEAR), tags=["year"])]


NOISE_LINES = [
    "Что думаешь, стоит ли сегодня заказать пиццу или лучше готовить самому?",
    "Погода сегодня отвратительная, весь день дождь.",
    "Посмотрел вчера новый фильм — так себе, не советую.",
    "Надо не забыть купить корм для кота на выходных.",
    "Работаю над отчётом, голова уже квадратная.",
    "Спасибо, что напомнил про встречу — чуть не забыл.",
    "Хочу на следующей неделе съездить на дачу, если погода наладится.",
    "Читаю сейчас книгу про историю Рима, довольно занятно.",
    "Надо записаться к стоматологу, давно откладываю.",
    "Купил новые кроссовки, очень удобные оказались.",
    "Планирую в отпуск в сентябре, ещё не решил куда.",
    "Сегодня на работе было особенно много встреч, устал.",
    "Заметил, что телефон стал быстрее разряжаться в последнее время.",
    "Начал бегать по утрам, пока держусь третью неделю.",
    "Пересматривал старые фотографии, ностальгия накрыла.",
]


def build_events(seed: int = 42, noise_ratio: float = 1.2) -> list[DialogEvent]:
    """Собирает полный список DialogEvent: по каждому члену семьи — факт о
    ДР, факт о возрасте, факт о работе, плюс факт про текущий год, вперемешку
    со случайным бытовым "шумом" (noise_ratio — сколько шумовых реплик на
    одну фактическую, в среднем). Порядок перемешан одним фиксированным
    seed, чтобы прогон был воспроизводим и сравним между запусками."""
    rnd = random.Random(seed)

    fact_events: list[DialogEvent] = []
    for m in FAMILY:
        fact_events.extend(_birthday_lines(m, rnd))
        fact_events.extend(_age_lines(m, rnd))
        fact_events.extend(_job_lines(m, rnd))
    fact_events.extend(_year_lines(rnd))
    # Повторяем "текущий год" ещё раз позже в диалоге — реалистично (люди
    # упоминают это не один раз) и полезно для проверки: должен находиться
    # оба раза, а после сжатия — хотя бы через саммари.
    fact_events.extend(_year_lines(rnd))

    n_noise = int(len(fact_events) * noise_ratio)
    noise_events = [
        DialogEvent(text=rnd.choice(NOISE_LINES), fact_key=None, fact_value=None, tags=["noise"])
        for _ in range(n_noise)
    ]

    all_events = fact_events + noise_events
    rnd.shuffle(all_events)
    return all_events


def get_dialog_lines(seed: int = 42, noise_ratio: float = 1.2) -> list[str]:
    return [e.text for e in build_events(seed=seed, noise_ratio=noise_ratio)]


def get_dialog_events(seed: int = 42, noise_ratio: float = 1.2) -> list[DialogEvent]:
    return build_events(seed=seed, noise_ratio=noise_ratio)


def expected_facts(year: int = CURRENT_YEAR) -> dict[str, str]:
    """Плоский словарь fact_key -> ожидаемое значение, для проверки после
    сжатия/поиска. Не зависит от перемешивания/шума."""
    out: dict[str, str] = {"meta.year": str(year)}
    for m in FAMILY:
        out[f"{m.key}.birthday"] = m.birthday_md
        out[f"{m.key}.age"] = str(age_of(m, year))
        out[f"{m.key}.job"] = m.job
    return out


if __name__ == "__main__":
    # Быстрый ручной просмотр: python3 scripts/family_dialog_generator.py
    for i, line in enumerate(get_dialog_lines(), 1):
        print(f"{i:3d}. {line}")
    print(f"\nВсего реплик: {len(get_dialog_lines())}")
    print(f"Фактов для проверки: {len(expected_facts())}")
