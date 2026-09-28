#!/usr/bin/env python3
"""Волны и рунбук итерации.

Читает .orchestrator/status.json, раскладывает тикеты итерации по волнам из deps,
проверяет, что тикеты одной волны не делят файлы, и пишет .orchestrator/runbook.md.
Рунбук руками больше не пишется: руками в нём ошибаются, а скрипт по тем же
deps и files ошибиться не может.

    python3 waves.py [путь-к-проекту] [--iteration N] [--dry] [--out ФАЙЛ]

--dry     только показать волны и ошибки, рунбук не трогать
--out     записать рунбук в другой файл
Код выхода 1 — есть ошибки нарезки. Рунбук при этом всё равно пишется, с
пометкой: человек не должен остаться без строк запуска из-за одной пары тикетов.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402

MARK = {"done": "готов", "doing": "в работе", "failed": "упал"}


def here() -> str:
    """Путь к скриптам навыка так, как его наберёт человек: с ~ вместо домашней папки."""
    p = pathlib.Path(__file__).resolve().parent
    try:
        return "~/" + str(p.relative_to(pathlib.Path.home()))
    except ValueError:
        return str(p)


def files_phrase(files: list) -> str:
    shown = ", ".join(f"`{f}`" for f in files[:2])
    return shown + (f" и ещё {len(files) - 2}" if len(files) > 2 else "")


def render(root: pathlib.Path, data: dict, plan: dict) -> str:
    it = plan["iteration"]
    every = {t["id"]: t for t in data.get("tickets", [])}
    title = state.titles(root, data).get(it)
    tools = data.get("tools") or {}
    out = []
    add = out.append

    add(f"# Рунбук — итерация {it}" + (f": {title}" if title else ""))
    add("")
    add(f"Собран скриптом из `status.json` {datetime.now().strftime('%d.%m.%Y %H:%M')}. "
        "Руками не править: поменялись зависимости или файлы тикетов — пересобрать.")
    add("")
    add("```")
    add(f"python3 {here()}/waves.py")
    add("```")
    add("")

    if plan["errors"] or plan["conflicts"]:
        add("## Ошибки нарезки — поправить до запуска")
        add("")
        for e in plan["errors"]:
            add(f"- {e}.")
        for c in plan["conflicts"]:
            add(f"- {c['a']} и {c['b']} стоят в волне {c['wave']} и оба правят "
                f"{files_phrase(c['files'])}. Запущенные вместе, они затрут друг друга: "
                f"поставь {c['b']} после {c['a']} или слей тикеты.")
        add("")

    add("Каждый тикет — в отдельном окне агента, в корне проекта: вставить строку "
        "запуска. Первым делом окно смотрит свой тикет через `status.py show` и `git log`.")
    add("")
    if tools.get("check"):
        add(f"Проверка проекта, которую прогоняет каждый тикет: `{tools['check']}`.")
    if tools.get("shots"):
        add(f"Снимки экранов: `{tools['shots']}`.")
    if tools.get("check") or tools.get("shots"):
        add("")

    r = plan["ready"]
    add("## Можно запустить сейчас")
    add("")
    if r["now"]:
        add("```")
        for tid in r["now"]:
            add(f"{tid}  {every[tid].get('title', '')}")
            add(f"     /son-of-orchestrator {tid}")
            add("")
        out[-1] = "```"
        add("")
        if len(r["now"]) > 1:
            add(f"Общих файлов у них нет — можно в {len(r['now'])} окнах сразу или фоновыми "
                "агентами: в основном окне написать «запусти волну».")
            add("")
    else:
        mine = [t for t in data.get("tickets", []) if state.it_of(t) == it]
        if mine and all(t.get("state") == "done" for t in mine):
            add("Все тикеты итерации закрыты. В любом окне написать «финиш».")
        elif r["doing"]:
            add("Ничего нового: идут " + ", ".join(r["doing"]) + ", остальные ждут их.")
        else:
            add("Ничего: все оставшиеся ждут зависимостей.")
        add("")
    for h in r["held"]:
        why = ("у одного из них нет списка файлов, пересечение не проверить" if h.get("unknown")
               else f"оба правят {files_phrase(h['files'])}")
        add(f"- {h['ticket']} готов по зависимостям, но ждёт {h['by']}: {why}.")
    if r["held"]:
        add("")
    waiting_ext = [e for e in plan["external"] if e["state"] != "done"]
    for e in waiting_ext:
        add(f"- {e['ticket']} ждёт {e['dep']} из итерации {e['iteration']}.")
    if waiting_ext:
        add("")

    add("## Волны")
    add("")
    for n, group in enumerate(plan["waves"], 1):
        head = "сразу" if n == 1 else f"после волны {n - 1}"
        add(f"### Волна {n} — {head}")
        add("")
        add("```")
        for tid in group:
            t = every[tid]
            mark = MARK.get(t.get("state"))
            ds = plan["deps"][tid]
            after = f"   (после {', '.join(ds)})" if ds else ""
            add(f"{tid}  {t.get('title', '')}{after}" + (f"   — {mark}" if mark else ""))
            add(f"     /son-of-orchestrator {tid}")
            add("")
        out[-1] = "```"
        add("")
        notes = []
        if len(group) > 1:
            clash = [c for c in plan["conflicts"] if c["wave"] == n]
            if not clash:
                notes.append(f"{', '.join(group[:-1])} и {group[-1]} — одновременно: общих файлов нет.")
        for tid in group:
            for d in plan["deps"][tid]:
                common = plan["why"].get(f"{tid}>{d}") or []
                reason = (f"оба правят {files_phrase(common)}" if common
                          else f"берёт готовое из {d}")
                notes.append(f"{tid} после {d}: {reason}.")
        for x in notes:
            add(f"- {x}")
        if notes:
            add("")

    if plan["unordered"]:
        add("## Порядок не задан")
        add("")
        add("Эти пары правят общие файлы, но ни один тикет не зависит от другого. Вместе "
            "их не запустят, а вот кто первый — случайность. Опирается один на правку "
            "другого — допиши зависимость.")
        add("")
        for u in plan["unordered"]:
            add(f"- {u['a']} и {u['b']}: {files_phrase(u['files'])}.")
        add("")

    if plan["no_files"]:
        add("## Без списка файлов")
        add("")
        add("У " + ", ".join(plan["no_files"]) + " нет списка файлов: пересечения с ними "
            "не проверить, и параллельно их запускать нельзя.")
        add("")

    add("## Дашборд")
    add("")
    add("```")
    add(f"python3 {here()}/dashboard.py --serve")
    add("```")
    add("")
    add("Когда все тикеты итерации закрыты — в любом окне написать «финиш».")
    return "\n".join(out) + "\n"


def report(plan: dict, every: dict) -> None:
    it = plan["iteration"]
    print(f"итерация {it}: {sum(len(w) for w in plan['waves'])} тикетов, "
          f"{len(plan['waves'])} волн")
    for n, group in enumerate(plan["waves"], 1):
        cells = []
        for tid in group:
            mark = {"done": "+", "doing": "~", "failed": "!"}.get(every[tid].get("state"), " ")
            cells.append(f"{tid}{mark}")
        print(f"  волна {n}: {'  '.join(cells)}")
    r = plan["ready"]
    print("  можно сейчас: " + (", ".join(r["now"]) or "ничего"))
    for h in r["held"]:
        why = "нет списка файлов" if h.get("unknown") else "общие файлы " + ", ".join(h["files"])
        print(f"  {h['ticket']} ждёт {h['by']}: {why}")
    for e in plan["external"]:
        if e["state"] != "done":
            print(f"  {e['ticket']} ждёт {e['dep']} из итерации {e['iteration']}")
    for e in plan["errors"]:
        print(f"  ОШИБКА: {e}")
    for c in plan["conflicts"]:
        print(f"  ОШИБКА: волна {c['wave']}: {c['a']} и {c['b']} правят {', '.join(c['files'])}")
    for u in plan["unordered"]:
        print(f"  порядок не задан: {u['a']} и {u['b']} правят {', '.join(u['files'][:3])}"
              + (f" и ещё {len(u['files']) - 3}" if len(u['files']) > 3 else ""))
    if plan["no_files"]:
        print("  без списка файлов: " + ", ".join(plan["no_files"]))


def main() -> None:
    ap = argparse.ArgumentParser(description="Волны и рунбук итерации son-of-orchestrator")
    ap.add_argument("root", nargs="?", default=".", help="корень проекта")
    ap.add_argument("--iteration", type=int, help="номер итерации (по умолчанию текущая)")
    ap.add_argument("--dry", action="store_true", help="только показать, рунбук не писать")
    ap.add_argument("--out", help="куда писать рунбук (по умолчанию .orchestrator/runbook.md)")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    data = state.load(root)
    plan = state.waves(data, args.iteration)
    every = {t["id"]: t for t in data.get("tickets", [])}
    if not plan["waves"]:
        sys.exit(f"В итерации {plan['iteration']} нет тикетов.")

    report(plan, every)
    if not args.dry:
        out = pathlib.Path(args.out) if args.out else state.orch(root) / "runbook.md"
        out.write_text(render(root, data, plan), encoding="utf-8")
        print(f"рунбук: {out}")
    sys.exit(1 if plan["errors"] or plan["conflicts"] else 0)


if __name__ == "__main__":
    main()
