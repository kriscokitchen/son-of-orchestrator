#!/usr/bin/env python3
"""Запись в status.json из окна тикета — короткой командой, под блокировкой.

Окна тикетов идут параллельно и пишут в один status.json. Модель, которая
правит JSON руками, читает файл, думает минуту и пишет обратно — всё, что за эту
минуту записало соседнее окно, пропадает. Здесь чтение и запись занимают
миллисекунды и идут под файлом-замком. Заодно окну не нужно читать весь
status.json ради одной записи: он растёт на тысячи строк за несколько итераций.

    python3 status.py show T25                 тикет, его зависимости, проверка проекта
    python3 status.py start T25                в работу, время старта, номер попытки
    python3 status.py check T25 3 pass         пункт приёмки 3: pass | fail | pending
    python3 status.py done T25 --commit abc123 закрыть; откажет, если не все пункты pass
    python3 status.py fail T25 "причина"       упал, причина одной строкой
    python3 status.py wait T25 "вопрос"        ждёт решения человека, обратно в todo
    python3 status.py phase 4                  фаза текущей итерации

Путь к проекту — текущая папка или --root.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
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


def show(root: pathlib.Path, data: dict, tid: str) -> None:
    t = find(data, tid)
    every = {x["id"]: x for x in data.get("tickets", [])}
    it = state.it_of(t)
    title = state.titles(root, data).get(it)
    print(f"{tid} — {t.get('title', '')}")
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Запись в status.json из окна тикета")
    ap.add_argument("--root", default=".", help="корень проекта")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("show", "start"):
        sub.add_parser(name).add_argument("ticket")
    c = sub.add_parser("check")
    c.add_argument("ticket")
    c.add_argument("n", type=int)
    c.add_argument("result", choices=("pass", "fail", "pending"))
    d = sub.add_parser("done")
    d.add_argument("ticket")
    d.add_argument("--commit")
    for name in ("fail", "wait"):
        p = sub.add_parser(name)
        p.add_argument("ticket")
        p.add_argument("reason")
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

        t = find(data, args.ticket)
        if args.cmd == "start":
            t["state"] = "doing"
            t["started"] = now()
            t.pop("finished", None)
            t["attempts"] = int(t.get("attempts") or 0) + 1
            msg = f"{t['id']} в работе, попытка {t['attempts']}"
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
            msg = f"{t['id']} упал: {args.reason}"
        else:  # wait
            t["state"] = "todo"
            t.pop("started", None)
            t["note"] = "ждёт решения: " + args.reason
            msg = f"{t['id']} ждёт решения человека"
        write(path, data)
        print(msg)


if __name__ == "__main__":
    main()
