#!/usr/bin/env python3
"""Запись в status.json из окна тикета — короткой командой, под блокировкой.

Окна тикетов идут параллельно и пишут в один status.json. Модель, которая
правит JSON руками, читает файл, думает минуту и пишет обратно — всё, что за эту
минуту записало соседнее окно, пропадает. Здесь чтение и запись занимают
миллисекунды и идут под файлом-замком. Заодно окну не нужно читать весь
status.json ради одной записи: он растёт на тысячи строк за несколько итераций.

    python3 status.py show T25                 тикет, его зависимости, проверка проекта
    python3 status.py start T25                в работу, время старта, номер попытки; откажет,
                                               если окно идёт на усилии ниже нужного тикету
        --effort-ok "причина"                  стартовать ниже: выше не пускает модель или лимит
        --agent                                тикет ушёл фоновому агенту с effort из show
    python3 status.py ran T25 medium           на каком усилии шёл фоновый агент — из его отчёта
    python3 status.py floor                    нижняя ступень усилия — уровень этого окна,
                                               то есть тот, на котором работает человек
    python3 status.py check T25 3 pass         пункт приёмки 3: pass | fail | pending
    python3 status.py done T25 --commit abc123 закрыть; откажет, если не все пункты pass
    python3 status.py fail T25 "причина"       упал, причина одной строкой
    python3 status.py wait T25 "вопрос" --option "А" --option "Б"
                                               ждёт решения человека, обратно в todo
    python3 status.py answer T25 "ответ"       человек ответил: вопрос снят
    python3 status.py about [T25 …]            блок «что считать готовым, зачем, почему так»
                                               и строка «Усилие» из файла тикета; без номеров —
                                               вся итерация
    python3 status.py summary "текст"          описание проекта своими словами
    python3 status.py mode ask|run             фоном: спрашивать после волны | вести до конца
    python3 status.py wave "итог" [--stop]     итог волны; в режиме ask — ждать ответа
    python3 status.py go                       человек ответил на итог волны: можно дальше
    python3 status.py phase 4                  фаза текущей итерации

Путь к проекту — текущая папка или --root.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Lock:
    """Файл-замок рядом со status.json. Работает одинаково на macOS, Linux и Windows."""

    def __init__(self, path: pathlib.Path):
        self.path = path

    def __enter__(self):
        deadline = time.time() + 15
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                # Окно упало, не сняв замок, — замок старше полуминуты считается брошенным.
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    sys.exit(f"status.json занят другим окном дольше 15 с: {self.path}")
                time.sleep(0.1)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def write(path: pathlib.Path, data: dict) -> None:
    data["updated"] = now()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".status-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)  # атомарно: читатель видит или старый файл, или новый


def find(data: dict, tid: str) -> dict:
    for t in data.get("tickets", []):
        if t.get("id") == tid:
            return t
    sys.exit(f"Тикета {tid} нет в status.json.")


# «Что делать» — прежнее название первой строки (2.1): такие тикеты тоже читаются.
ABOUT = (("what", "Что считать готовым"), ("why", "Зачем"), ("how", "Почему так"))
OLD = {"что делать": "what", "усилие": "effort"}
LABEL = re.compile(r"^\s*(?:[-*]\s+)?\**\s*(Что считать готовым|Что делать|Зачем|Почему так|Усилие)\s*\**\s*:\s*\**\s*(.*)$", re.I)
EFFORT = re.compile(r"^`?([a-z]+)`?\s*(?:[—–-]+\s*)?(.*)$", re.I)


def read_about(path: pathlib.Path) -> dict:
    """Блок из начала файла тикета: три строки и «Усилие». Строка может
    переноситься: продолжение идёт до пустой строки, заголовка или следующей метки."""
    keys = {**{label.lower(): key for key, label in ABOUT}, **OLD}
    out, cur = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LABEL.match(line)
        if m and keys[m.group(1).lower()] not in out:
            cur = keys[m.group(1).lower()]
            out[cur] = m.group(2).strip()
        elif cur and line.strip() and not line.lstrip().startswith("#"):
            out[cur] += " " + line.strip()
        else:
            cur = None
            if out and line.startswith("## "):
                break  # блок кончился: ниже тело тикета
        if len(out) == 4 and cur is None:
            break
    return {k: v.strip(" *") for k, v in out.items() if v.strip(" *")}


def parse_effort(text: str) -> tuple[str, str]:
    """«high — правит src/plan.js из T12» → ("high", "правит src/plan.js из T12")."""
    m = EFFORT.match(text.strip())
    return (m.group(1).lower(), m.group(2).strip()) if m else ("", text.strip())


def window_effort() -> str | None:
    """Уровень, на котором идёт это окно или этот фоновый агент. Claude Code кладёт
    его в окружение Bash уже после тихого понижения под модель; в других агентах
    переменной нет — уровень неизвестен."""
    v = (os.environ.get("CLAUDE_EFFORT") or "").strip().lower()
    return v if v in state.LEVELS else None


def raise_hint(need: str) -> str:
    """Как поднять уровень окна, не испортив человеку настройки по умолчанию."""
    pinned = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    if pinned:
        return (f"Уровень зафиксирован переменной CLAUDE_CODE_EFFORT_LEVEL={pinned}, /effort её "
                f"не перебьёт: открой окно без неё. ")
    return (f"Подними его только для этого окна: /effort → {need} → клавиша s. Не набирай "
            f"«/effort {need}» текстом: так уровень сохранится по умолчанию для всех будущих "
            f"окон. ")


def show(root: pathlib.Path, data: dict, tid: str) -> None:
    t = find(data, tid)
    every = {x["id"]: x for x in data.get("tickets", [])}
    it = state.it_of(t)
    title = state.titles(root, data).get(it)
    print(f"{tid} — {t.get('title', '')}")
    # Блок о тикете — первым: окно сначала понимает, что и зачем делает.
    about = t.get("about") or {}
    for key, label in ABOUT:
        if about.get(key):
            print(f"{label.lower()}: {about[key]}")
    e, need = t.get("effort") or {}, state.effort_need(t, state.effort_floor(data))
    if need:
        print(f"усилие: {e['level']}" + (f" — {e['why']}" if e.get("why") else ""))
        if need != e["level"]:
            why = ("поднят после падения" if need == state.effort_need(t)
                   else "не ниже уровня, на котором работает человек")
            print(f"сейчас нужно: {need} — {why}")
        have = window_effort()
        if state.effort_below(have, need):
            print(f"УСИЛИЕ НИЖЕ НУЖНОГО: это окно на {have}, тикет просит {need}. "
                  + raise_hint(need))
        elif have is None:
            print("уровень этого окна неизвестен: переменной CLAUDE_EFFORT нет")
    if state.asking(t):
        print(f"ЖДЁТ РЕШЕНИЯ: {t['ask']['question']}")
        for i, o in enumerate(t["ask"].get("options") or [], 1):
            print(f"  {i}. {o}")
    print(f"итерация {it}" + (f": {title}" if title else "")
          + (f" (текущая {state.current(data)})" if it != state.current(data) else ""))
    print(f"состояние {t.get('state', 'todo')}, попыток {t.get('attempts', 0)}")
    f = state.ticket_file(root, tid)
    print(f"файл тикета: {f.relative_to(root) if f else 'не найден'}")
    deps = [d for d in (t.get("deps") or []) if d != state.ALL]
    if state.ALL in (t.get("deps") or []):
        deps += [x["id"] for x in data["tickets"]
                 if state.it_of(x) == it and x["id"] != tid and state.ALL not in (x.get("deps") or [])]
    if deps:
        print("зависит от: " + ", ".join(
            f"{d} {every[d].get('state', '?') if d in every else 'НЕТ ТАКОГО'}" for d in dict.fromkeys(deps)))
    open_deps = [d for d in deps if d not in every or every[d].get("state") != "done"]
    if open_deps:
        print("НЕ ГОТОВ: не закрыты " + ", ".join(open_deps))
    print("файлы:")
    for p in t.get("files") or []:
        print(f"  {p}")
    doing = [x for x in data["tickets"] if x.get("state") == "doing" and x["id"] != tid]
    for x in doing:
        common = state.overlap(t.get("files"), x.get("files"))
        if common:
            print(f"ЗАНЯТО: {x['id']} в работе и правит {', '.join(common)}")
    print("приёмка:")
    for i, c in enumerate(t.get("checks") or [], 1):
        print(f"  {i}. [{c.get('state', 'pending')}] {c.get('text', '')}")
    tools = data.get("tools") or {}
    if tools.get("check"):
        print(f"проверка проекта: {tools['check']}")
    if tools.get("shots"):
        print(f"снимки: {tools['shots']}")
    if tools.get("probe"):
        print(f"проверка поведения: {tools['probe']} <шаги>")


def main() -> None:
    ap = argparse.ArgumentParser(description="Запись в status.json из окна тикета")
    ap.add_argument("--root", default=".", help="корень проекта")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("ticket")
    s = sub.add_parser("start")
    s.add_argument("ticket")
    s.add_argument("--effort-ok", metavar="ПРИЧИНА",
                   help="стартовать на усилии ниже нужного: выше не пускает модель или лимит")
    s.add_argument("--agent", action="store_true",
                   help="тикет ушёл фоновому агенту, которому передан нужный effort")
    c = sub.add_parser("check")
    c.add_argument("ticket")
    c.add_argument("n", type=int)
    c.add_argument("result", choices=("pass", "fail", "pending"))
    d = sub.add_parser("done")
    d.add_argument("ticket")
    d.add_argument("--commit")
    for name in ("fail", "wait", "answer"):
        p = sub.add_parser(name)
        p.add_argument("ticket")
        p.add_argument("reason")
        if name == "wait":
            p.add_argument("--option", action="append", default=[], help="вариант ответа")
    sub.add_parser("about").add_argument("tickets", nargs="*")
    r = sub.add_parser("ran")
    r.add_argument("ticket")
    r.add_argument("level", choices=state.LEVELS)
    sub.add_parser("summary").add_argument("text")
    sub.add_parser("mode").add_argument("mode", choices=("ask", "run"))
    w = sub.add_parser("wave")
    w.add_argument("text")
    w.add_argument("--stop", action="store_true", help="остановиться и ждать ответа и в режиме run")
    sub.add_parser("go")
    sub.add_parser("floor")
    ph = sub.add_parser("phase")
    ph.add_argument("n", type=int)
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    path = state.orch(root) / "status.json"

    if args.cmd == "show":
        show(root, state.load(root), args.ticket)
        return

    with Lock(path.with_suffix(".lock")):
        data = state.load(root)  # читаем под замком: всё, что успели записать другие окна
        if args.cmd == "phase":
            if not 0 <= args.n < len(state.PHASES):
                sys.exit(f"Фазы {args.n} нет: 0–{len(state.PHASES) - 1}.")
            data["phase"] = {"current": args.n, "name": state.PHASES[args.n], "started": now()}
            write(path, data)
            print(f"фаза {args.n}: {state.PHASES[args.n]}")
            return
        if args.cmd == "summary":
            data["summary"] = args.text.strip()
            write(path, data)
            print("описание проекта записано")
            return
        if args.cmd == "floor":
            have = window_effort()
            if have is None:
                sys.exit("Уровень этого окна неизвестен (переменной CLAUDE_EFFORT нет): "
                         "нижняя ступень не записана, тикеты пойдут по правилам нарезки.")
            data["effort_floor"] = have
            write(path, data)
            got = state.effort_floor(data)
            print(f"нижняя ступень усилия: {got}" + (f" (окно на {have}, выше xhigh пол не держим)"
                                                      if got != have else ""))
            return
        if args.cmd in ("mode", "wave", "go"):
            bg = data.setdefault("background", {})
            if args.cmd == "mode":
                bg["mode"] = args.mode
                msg = "фоном: " + ("спрашивать после каждой волны" if args.mode == "ask"
                                   else "вести до конца итерации")
            elif args.cmd == "wave":
                bg["last"] = {"iteration": state.current(data), "at": now(), "text": args.text.strip()}
                # Флаг в файле, а не в памяти окна: контекст основного окна
                # сжимается, а вопрос после волны должен пережить сжатие.
                # После последней волны ждать нечего: флаг держал бы «ждём ответа»
                # над закрытой итерацией.
                cur = state.current(data)
                left = any(t.get("state") != "done" for t in data.get("tickets", [])
                           if state.it_of(t) == cur)
                bg["waiting"] = left and (args.stop or bg.get("mode", "ask") != "run")
                msg = "итог волны записан" + (", ждём ответа человека" if bg["waiting"] else "")
            else:
                bg["waiting"] = False
                msg = "можно запускать следующую волну"
            write(path, data)
            print(msg)
            return
        if args.cmd == "about":
            cur = state.current(data)
            ids = args.tickets or [t["id"] for t in data.get("tickets", []) if state.it_of(t) == cur]
            missing, bad = [], []
            for tid in ids:
                t = find(data, tid)
                f = state.ticket_file(root, tid)
                about = read_about(f) if f else {}
                raw = about.pop("effort", None)
                if about:
                    t["about"] = about
                if len(about) < 3:
                    missing.append(tid + ("" if f else " (нет файла)"))
                if raw is None:
                    if f:
                        bad.append(f"{tid}: нет строки «Усилие»")
                    continue
                level, why = parse_effort(raw)
                if level not in state.LEVELS:
                    bad.append(f"{tid}: «{level or raw}» — не уровень ({', '.join(state.LEVELS[1:])})")
                elif level == "low":
                    bad.append(f"{tid}: low тикетам не выдаётся — тикет идёт без человека, "
                               "а на low модель пропускает проверки")
                elif not why:
                    bad.append(f"{tid}: у уровня {level} нет признака, по которому он выбран")
                else:
                    # bump и ran живут в json: файл тикета их не знает и не должен затирать.
                    # Тикет переписали (уровень или признак другие) — это новое решение:
                    # подъём за падения и запуски прежнего тикета к нему не относятся.
                    old = t.get("effort") or {}
                    if (old.get("level"), old.get("why")) != (level, why):
                        old = {}
                    t["effort"] = {**old, "level": level, "why": why}
            write(path, data)
            print(f"блок о тикете записан: {len(ids) - len(missing)} из {len(ids)}")
            if missing:
                print("без полного блока «Что считать готовым / Зачем / Почему так»: " + ", ".join(missing))
            for b in bad:
                print("усилие не записано — " + b)
            return

        t = find(data, args.ticket)
        if args.cmd in ("start", "done", "fail"):
            t.pop("ask", None)  # вопрос снят: тикет пошёл дальше
        if args.cmd == "start":
            need, e = state.effort_need(t, state.effort_floor(data)), t.get("effort")
            have = None if args.agent else window_effort()
            if not args.agent and not args.effort_ok and state.effort_below(have, need):
                # Гейт: тикет, нарезанный под xhigh, на medium проверит меньше краёв
                # и примет больше решений наугад — и выглядеть это будет как «готово».
                sys.exit(f"{t['id']} не начат: тикет просит усилие {need}, а это окно идёт на "
                         f"{have}. " + raise_hint(need) + "Потом повтори start. Выше не "
                         f"пускает модель или ограничение — status.py start {t['id']} "
                         f"--effort-ok \"причина\".")
            if e is not None:
                # У фонового агента уровень известен только из его отчёта (status.py ran):
                # переданный параметр могла перебить переменная окружения или лимит.
                if args.agent:
                    e.pop("ran", None)
                else:
                    e["ran"] = have
                if args.effort_ok and state.effort_below(have, need):
                    e["override"] = args.effort_ok
                else:
                    e.pop("override", None)
            t["state"] = "doing"
            t["started"] = now()
            t.pop("finished", None)
            t["attempts"] = int(t.get("attempts") or 0) + 1
            msg = f"{t['id']} в работе, попытка {t['attempts']}"
        elif args.cmd == "ran":
            need, e = state.effort_need(t, state.effort_floor(data)), t.get("effort")
            if e is None:
                sys.exit(f"У {t['id']} нет уровня усилия: записывать не к чему.")
            e["ran"] = args.level
            if state.effort_below(args.level, need):
                e["override"] = "фоновый агент шёл ниже: переданный уровень перебила переменная или лимит"
                msg = f"{t['id']} шёл на {args.level} при нужном {need} — скажи человеку в итоге волны"
            else:
                e.pop("override", None)
                msg = f"{t['id']}: шёл на {args.level}"
        elif args.cmd == "check":
            checks = t.get("checks") or []
            if not 1 <= args.n <= len(checks):
                sys.exit(f"У {t['id']} пунктов приёмки {len(checks)}, пункта {args.n} нет.")
            checks[args.n - 1]["state"] = args.result
            msg = f"{t['id']} пункт {args.n}: {args.result}"
        elif args.cmd == "done":
            left = [i for i, ch in enumerate(t.get("checks") or [], 1) if ch.get("state") != "pass"]
            if left:
                # Закрытый тикет с непройденным пунктом — это «готово» о том, что не проверяли.
                sys.exit(f"{t['id']} не закрыт: пункты {', '.join(map(str, left))} не pass.")
            t["state"] = "done"
            t["finished"] = now()
            if args.commit:
                t["commit"] = args.commit
            msg = f"{t['id']} готов"
        elif args.cmd == "fail":
            t["state"] = "failed"
            t["finished"] = now()
            t["note"] = args.reason
            if state.effort_need(t):
                # Следующая попытка — ступенью выше: две попытки на этом уровне
                # упёрлись в одно и то же. «Нужно решение» (wait) уровень не трогает.
                t["effort"]["bump"] = int(t["effort"].get("bump") or 0) + 1
            msg = f"{t['id']} упал: {args.reason}"
            if state.effort_need(t):
                msg += f"; следующая попытка — на усилии {state.effort_need(t, state.effort_floor(data))}"
        elif args.cmd == "answer":
            t.pop("ask", None)
            t["note"] = "решение: " + args.reason
            msg = f"{t['id']}: ответ записан, тикет снова можно запускать"
        else:  # wait
            t["state"] = "todo"
            t.pop("started", None)
            t["note"] = "ждёт решения: " + args.reason
            # Вопрос с вариантами — для дашборда и для окна, которое спросит человека.
            t["ask"] = {"question": args.reason, "options": args.option, "at": now()}
            msg = f"{t['id']} ждёт решения человека"
        write(path, data)
        print(msg)


if __name__ == "__main__":
    main()
