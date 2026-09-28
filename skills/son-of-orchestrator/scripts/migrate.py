#!/usr/bin/env python3
"""Перевод проекта старой раскладки в папки итераций.

До режима итераций проект жил так: тикеты в .orchestrator/tickets/, итерации
дописаны разделами «# Итерация N» в конец interview.md и spec.md, отчёт прошлой
итерации переименован в ключ report_iN. Скрипты навыка читают это как есть,
поэтому мигрировать не обязательно. Этот скрипт — для тех, кто хочет разложить
историю по папкам.

    python3 migrate.py [путь-к-проекту]            показать, что будет сделано
    python3 migrate.py [путь-к-проекту] --apply    сделать

Пока какой-то тикет в работе, --apply откажет: окно этого тикета в ту же минуту
пишет в status.json и читает свой файл тикета по старому пути. --force — если
точно знаешь, что окна закрыты.

Оригиналы status.json, interview.md и runbook.md сохраняются в
iterations/до-миграции/. spec.md не трогается: свести историю итераций в живую
спеку — смысловая работа, её делает агент. Этот шаг записывается в
iterations/MIGRATION.md.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402
import waves  # noqa: E402

HEAD = re.compile(r"^# Итерация (\d+)\b.*$", re.M)


def split(text: str) -> dict:
    """Текст до первого «# Итерация N» — итерация 1, дальше — по заголовкам."""
    parts, marks = {}, list(HEAD.finditer(text))
    if not marks:
        return {1: text}
    head = text[:marks[0].start()].rstrip() + "\n"
    if head.strip():
        parts[1] = head
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        parts[int(m.group(1))] = text[m.start():end].rstrip() + "\n"
    return parts


def tracked(root: pathlib.Path, path: pathlib.Path) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], cwd=root,
                       capture_output=True, text=True)
    return r.returncode == 0


def move(root: pathlib.Path, src: pathlib.Path, dst: pathlib.Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if tracked(root, src):
        subprocess.run(["git", "mv", str(src), str(dst)], cwd=root, check=True,
                       capture_output=True)
    else:
        shutil.move(str(src), str(dst))


def main() -> None:
    ap = argparse.ArgumentParser(description="Перевод проекта в папки итераций")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--apply", action="store_true", help="выполнить; без флага — только показать")
    ap.add_argument("--force", action="store_true", help="выполнить, даже если тикет в работе")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    base = state.orch(root)
    data = state.load(root)
    its = base / "iterations"
    steps, todo = [], []

    doing = [t["id"] for t in data.get("tickets", []) if t.get("state") == "doing"]

    # 1. Поля iteration
    miss_t = [t["id"] for t in data.get("tickets", []) if t.get("iteration") is None]
    miss_r = [r["id"] for r in data.get("requirements", []) if r.get("iteration") is None]
    if miss_t or miss_r:
        steps.append(f"status.json: iteration = 1 у {len(miss_t)} тикетов и {len(miss_r)} требований "
                     f"({', '.join(miss_t[:3])}{' …' if len(miss_t) > 3 else ''})")
    if data.get("iteration") is None:
        steps.append(f"status.json: iteration = {state.current(data)}")
    names = state.titles(root, data)
    if not data.get("iterations") and names:
        steps.append("status.json: названия итераций из заголовков spec.md — "
                     + "; ".join(f"{k}: {v}" for k, v in sorted(names.items())))

    # 2. Отчёты report_iN
    old_reports = {int(m.group(1)): k for k in data
                   if (m := re.fullmatch(r"report_i(\d+)", k))}
    for n, k in sorted(old_reports.items()):
        steps.append(f"status.json: {k} → iterations/i{n}/report.json")

    # 3. Файлы тикетов
    moves = []
    if (base / "tickets").is_dir():
        known = {t["id"]: state.it_of(t) for t in data.get("tickets", [])}
        for f in sorted((base / "tickets").glob("T*.md")):
            n = known.get(f.stem)
            if n is None:
                todo.append(f"Файл {f.name} есть, а тикета {f.stem} в status.json нет — "
                            "разберись руками, скрипт его не трогал.")
                continue
            moves.append((f, its / f"i{n}" / "tickets" / f.name))
        if moves:
            per = {}
            for _, d in moves:
                per.setdefault(d.parent.parent.name, 0)
                per[d.parent.parent.name] += 1
            steps.append("тикеты: tickets/ → " + ", ".join(f"{k}/tickets ({v})" for k, v in sorted(per.items())))

    # 4. interview.md по итерациям
    iv = base / "interview.md"
    iv_parts = split(iv.read_text(encoding="utf-8")) if iv.exists() else {}
    if iv_parts:
        steps.append("interview.md → " + ", ".join(f"i{n}/interview.md" for n in sorted(iv_parts)))

    # 5. Разделы итераций из spec.md — копией в дельты
    sp = base / "spec.md"
    sp_parts = split(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    deltas = {n: t for n, t in sp_parts.items() if n > 1}
    if deltas:
        steps.append("spec.md: разделы итераций копией в " + ", ".join(
            f"i{n}/spec-delta.md" for n in sorted(deltas)) + " (spec.md не меняется)")
        todo.append("Свести живую спеку. В spec.md всё ещё лежат разделы «# Итерация "
                    + ", ".join(map(str, sorted(deltas))) + "». Перенеси принятое в основные "
                    "разделы как описание продукта, отменённое выбрось, разделы итераций "
                    "удали из spec.md — они сохранены в iterations/iN/spec-delta.md. "
                    "Порядок — phases/iteration.md.")
    if (base / "shape.md").exists():
        todo.append("Проверить shape.md: разделы, добавленные итерациями, скрипт отличить не "
                    "может. Если такие есть — вынеси их в iterations/iN/shape-delta.md, в "
                    "shape.md оставь живое направление.")

    # 6. Рунбук
    steps.append("runbook.md: старый — в iterations/до-миграции/, новый собирает waves.py")

    if not steps:
        print("Проект уже в раскладке по итерациям — делать нечего.")
        return

    print("Миграция" + (" (выполняется)" if args.apply else " — сухой прогон, ничего не меняется") + ":")
    for s in steps:
        print(f"  {s}")
    if todo:
        print("Останется руками:")
        for s in todo:
            print(f"  {s}")

    if not args.apply:
        print("\nВыполнить: python3 migrate.py --apply")
        return
    if doing and not args.force:
        sys.exit(f"\nНе выполняю: в работе {', '.join(doing)}. Окно этого тикета пишет в status.json "
                 "и читает файл тикета по старому пути. Дождись закрытия или --force, если окна закрыты.")

    # ── выполнение ──
    bak = its / "до-миграции"
    bak.mkdir(parents=True, exist_ok=True)
    for name in ("status.json", "interview.md", "runbook.md"):
        if (base / name).exists():
            shutil.copy2(base / name, bak / name)

    for t in data.get("tickets", []):
        t.setdefault("iteration", 1)
    for r in data.get("requirements", []):
        r.setdefault("iteration", 1)
    data["iteration"] = state.current(data)
    if not data.get("iterations") and names:
        data["iterations"] = {str(k): {"title": v} for k, v in sorted(names.items())}
    for n, k in old_reports.items():
        (its / f"i{n}").mkdir(parents=True, exist_ok=True)
        (its / f"i{n}" / "report.json").write_text(
            json.dumps(data.pop(k), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    (base / "status.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")

    for src, dst in moves:
        move(root, src, dst)
    if (base / "tickets").is_dir() and not any((base / "tickets").iterdir()):
        (base / "tickets").rmdir()

    for n, text in iv_parts.items():
        d = its / f"i{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "interview.md").write_text(text, encoding="utf-8")
    if iv_parts:
        if tracked(root, iv):
            subprocess.run(["git", "rm", "-q", "--cached", str(iv)], cwd=root, check=True)
        iv.unlink()

    for n, text in deltas.items():
        (its / f"i{n}" / "spec-delta.md").write_text(text, encoding="utf-8")

    plan = state.waves(data)
    (base / "runbook.md").write_text(waves.render(root, data, plan), encoding="utf-8")

    note = ["# Миграция в папки итераций", "",
            f"Выполнена {datetime.now().strftime('%d.%m.%Y %H:%M')} скриптом migrate.py. "
            "Оригиналы — в `до-миграции/`.", ""]
    note += ["## Сделано", ""] + [f"- {s}" for s in steps] + [""]
    if todo:
        note += ["## Осталось — миграция не закончена, пока это открыто", ""] + [f"- [ ] {s}" for s in todo] + [""]
    (its / "MIGRATION.md").write_text("\n".join(note), encoding="utf-8")
    print(f"\nГотово. Открытые шаги — в {its / 'MIGRATION.md'}")


if __name__ == "__main__":
    main()
