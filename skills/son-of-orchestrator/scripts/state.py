"""Состояние прогона: чтение status.json, итерации, метрики, волны.

Общий модуль для dashboard.py, waves.py и migrate.py — чтобы рунбук, дашборд и
миграция считали одно и то же одним кодом.

Читает и новую раскладку (.orchestrator/iterations/iN/), и старую, в которой
проект жил до итераций: у тикета или требования нет поля iteration — значит,
первая итерация; отчёт прошлой итерации лежит ключом report_iN прямо в
status.json; файлы тикетов лежат в .orchestrator/tickets/. Мигрировать проект,
чтобы им пользоваться, не нужно.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import datetime

PHASES = [
    "Проверка окружения",
    "Вопросы",
    "Спека",
    "Направление",
    "Задачи",
    "Раздача и сборка",
    "Финиш",
]
TICKET_STATES = ("todo", "doing", "done", "failed")
ALL = "*"  # deps: ["*"] — после всех остальных тикетов этой итерации
NOFILES = "\0нет списка файлов"  # метка «пересечение не проверить»


def orch(root: pathlib.Path) -> pathlib.Path:
    return root / ".orchestrator"


def load(root: pathlib.Path) -> dict:
    path = orch(root) / "status.json"
    if not path.exists():
        sys.exit(f"Нет {path}. Состояние прогона заводится на Фазе 0.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"{path} повреждён: {exc}")
    return data


def it_of(item: dict) -> int:
    """Итерация тикета или требования. Нет поля — первая: так жили до итераций."""
    v = item.get("iteration")
    try:
        return int(v) if v is not None else 1
    except (TypeError, ValueError):
        return 1


def iterations(data: dict) -> list:
    seen = {it_of(t) for t in data.get("tickets", [])}
    seen |= {it_of(r) for r in data.get("requirements", [])}
    cur = current(data)
    seen.add(cur)
    return sorted(seen)


def current(data: dict) -> int:
    v = data.get("iteration")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    its = [it_of(t) for t in data.get("tickets", [])] or [1]
    return max(its)


def titles(root: pathlib.Path, data: dict) -> dict:
    """Названия итераций: из status.iterations, иначе из заголовков spec.md.

    Второе — для проектов, которые дописывали итерации в конец спеки разделами
    «# Итерация N — …» до появления этого режима.
    """
    out = {}
    meta = data.get("iterations") or {}
    if isinstance(meta, dict):
        for k, v in meta.items():
            if isinstance(v, dict) and v.get("title"):
                out[int(k)] = v["title"]
    spec = orch(root) / "spec.md"
    if spec.exists():
        for m in re.finditer(r"^# Итерация (\d+)\s*[—-]\s*(.+?)\s*$",
                             spec.read_text(encoding="utf-8"), re.M):
            out.setdefault(int(m.group(1)), re.sub(r"\s*\(\d{2}\.\d{2}\.\d{4}\)$", "", m.group(2)))
    return out


def reports(root: pathlib.Path, data: dict) -> dict:
    """Отчёты по итерациям: текущий — status.report, прошлые — из архива."""
    out = {}
    for k, v in data.items():
        m = re.fullmatch(r"report_i(\d+)", k)
        if m and isinstance(v, dict):
            out[int(m.group(1))] = v
    base = orch(root) / "iterations"
    if base.exists():
        for f in base.glob("i*/report.json"):
            m = re.fullmatch(r"i(\d+)", f.parent.name)
            if m:
                try:
                    out[int(m.group(1))] = json.loads(f.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
    if isinstance(data.get("report"), dict):
        out[current(data)] = data["report"]
    return out


def ticket_file(root: pathlib.Path, tid: str) -> pathlib.Path | None:
    base = orch(root)
    hits = sorted(base.glob(f"iterations/i*/tickets/{tid}.md"))
    if hits:
        return hits[0]
    old = base / "tickets" / f"{tid}.md"
    return old if old.exists() else None


# ── время ────────────────────────────────────────────────────────────────────

def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def minutes(t: dict):
    """Сколько шёл тикет, в минутах. Смешанные метки с поясом и без — сравниваем без пояса."""
    a, b = _ts(t.get("started")), _ts(t.get("finished"))
    if not a or not b:
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return max(0, round((b - a).total_seconds() / 60))


# ── волны ────────────────────────────────────────────────────────────────────

def _norm(p: str) -> str:
    p = str(p).strip().strip("`")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def overlap(a: list, b: list) -> list:
    """Общие файлы двух тикетов. Каталог пересекается со всем, что внутри него."""
    out = []
    for x in map(_norm, a or []):
        for y in map(_norm, b or []):
            if x == y or y.startswith(x + "/") or x.startswith(y + "/"):
                out.append(x if len(x) >= len(y) else y)
    return sorted(set(out))


def waves(data: dict, it: int | None = None) -> dict:
    """Волны итерации по deps и files.

    Волна тикета — на единицу глубже самой глубокой его зависимости внутри
    итерации. Зависимость на тикет прошлой итерации волну не сдвигает: либо она
    выполнена, либо тикет ждёт её отдельно.

    Ошибка нарезки — два тикета одной волны на одном файле: их запустят
    одновременно, и одно окно молча затрёт другое.
    """
    it = current(data) if it is None else it
    every = {t["id"]: t for t in data.get("tickets", [])}
    mine = [t for t in data.get("tickets", []) if it_of(t) == it]
    ids = {t["id"] for t in mine}
    errors, external = [], []

    deps = {}
    for t in mine:
        raw = list(t.get("deps") or [])
        if ALL in raw:
            raw = [x for x in raw if x != ALL] + [
                o["id"] for o in mine if o["id"] != t["id"] and ALL not in (o.get("deps") or [])]
        inner = []
        for d in dict.fromkeys(raw):
            if d in ids:
                inner.append(d)
            elif d in every:
                external.append((t["id"], d, it_of(every[d]), every[d].get("state")))
            else:
                errors.append(f"{t['id']} зависит от {d}, а такого тикета нет")
        deps[t["id"]] = inner

    level, state = {}, {}

    def depth(tid, path=()):
        if tid in level:
            return level[tid]
        if tid in path:
            cyc = " → ".join(path[path.index(tid):] + (tid,))
            errors.append(f"цикл в зависимостях: {cyc}")
            return 1
        level[tid] = 1 + max((depth(d, path + (tid,)) for d in deps[tid]), default=0)
        return level[tid]

    for t in mine:
        depth(t["id"])

    order = {t["id"]: i for i, t in enumerate(mine)}
    groups = {}
    for tid, w in level.items():
        groups.setdefault(w, []).append(tid)
    wave_list = [sorted(groups[w], key=order.get) for w in sorted(groups)]

    conflicts = []
    for n, group in enumerate(wave_list, 1):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                common = overlap(every[a].get("files"), every[b].get("files"))
                if common:
                    conflicts.append({"wave": n, "a": a, "b": b, "files": common})

    # Разные волны, общий файл, и ни один не зависит от другого даже через
    # цепочку. Вместе их не запустят — готовые к старту проверяются на общие
    # файлы, — но кто из них первый, не задано. Если второй опирается на правку
    # первого, это пропущенная зависимость.
    reach = {}

    def above(tid, seen=()):
        if tid in reach:
            return reach[tid]
        acc = set()
        for d in deps[tid]:
            if d not in seen:
                acc |= {d} | above(d, seen + (tid,))
        reach[tid] = acc
        return acc

    unordered = []
    flat = [t["id"] for t in mine]
    for i, a in enumerate(flat):
        for b in flat[i + 1:]:
            if level.get(a) == level.get(b) or a in above(b) or b in above(a):
                continue
            common = overlap(every[a].get("files"), every[b].get("files"))
            if common:
                unordered.append({"a": a, "b": b, "files": common})

    no_files = [t["id"] for t in mine if not t.get("files")]

    # Причина каждой зависимости: общие файлы или «берёт результат».
    why = {}
    for t in mine:
        for d in deps[t["id"]]:
            why[(t["id"], d)] = overlap(t.get("files"), every[d].get("files"))

    return {
        "iteration": it,
        "waves": wave_list,
        "level": level,
        "deps": deps,
        "why": {f"{a}>{b}": f for (a, b), f in why.items()},
        "external": [{"ticket": a, "dep": d, "iteration": i, "state": s} for a, d, i, s in external],
        "conflicts": conflicts,
        "unordered": unordered,
        "errors": errors,
        "no_files": no_files,
        "ready": ready(data, it, deps),
    }


def clash(a: dict, b: dict) -> list:
    """Общие файлы двух тикетов. Нет списка у одного из них — пересечение не
    проверить, и параллельно их вести нельзя: считаем, что делят всё."""
    if not a.get("files") or not b.get("files"):
        return [NOFILES]
    return overlap(a.get("files"), b.get("files"))


def ready(data: dict, it: int, deps: dict | None = None) -> dict:
    """Что можно запустить прямо сейчас и что держит остальное.

    Готов — не начат, все зависимости (и из прошлых итераций) закрыты. Из
    готовых набираются те, что не делят файлы ни друг с другом, ни с тикетами в
    работе: это и есть набор для параллельных окон или фоновых агентов.
    """
    every = {t["id"]: t for t in data.get("tickets", [])}
    mine = [t for t in data.get("tickets", []) if it_of(t) == it]
    if deps is None:
        deps = {t["id"]: [d for d in (t.get("deps") or []) if d != ALL] for t in mine}
    doing = [t for t in data.get("tickets", []) if t.get("state") == "doing"]

    def all_deps(t):
        raw = list(t.get("deps") or [])
        extra = [d for d in raw if d != ALL and d not in deps.get(t["id"], [])]
        return list(deps.get(t["id"], [])) + extra

    now, held = [], []
    for t in mine:
        if t.get("state") not in (None, "todo"):
            continue
        # Зависимость на несуществующий тикет не выполнится никогда: такой
        # тикет не готов, пока нарезку не поправят.
        waiting = [d for d in all_deps(t) if d not in every or every[d].get("state") != "done"]
        if waiting:
            continue
        busy = [(o["id"], clash(t, o)) for o in doing + now]
        busy = [(o, f) for o, f in busy if f]
        if busy:
            unknown = busy[0][1] == [NOFILES]
            held.append({"ticket": t["id"], "by": busy[0][0],
                         "files": [] if unknown else busy[0][1], "unknown": unknown})
        else:
            now.append(t)
    return {"now": [t["id"] for t in now], "held": held,
            "doing": [t["id"] for t in doing]}


# ── метрики ──────────────────────────────────────────────────────────────────

def metrics(data: dict, it: int | None = None) -> dict:
    it = current(data) if it is None else it
    tickets = [t for t in data.get("tickets", []) if it_of(t) == it]
    reqs = [r for r in data.get("requirements", []) if it_of(r) == it]

    by_state = {s: sum(1 for t in tickets if t.get("state") == s) for s in TICKET_STATES}
    total = len(tickets)
    scored = [r for r in reqs if r.get("state") != "out-of-scope"]
    pending = sum(1 for r in scored if r.get("state") == "pending")
    confirmed = sum(1 for r in scored if r.get("state") == "confirmed")
    checks = [c for t in tickets for c in t.get("checks", [])]

    phase = data.get("phase", {}) or {}
    cur = phase.get("current")
    if it < current(data):
        cur = len(PHASES)  # прошлая итерация пройдена целиком

    mins = [minutes(t) for t in tickets if t.get("state") == "done"]
    mins = [m for m in mins if m is not None]

    return {
        "iteration": it,
        "phase_current": cur,
        "phase_name": phase.get("name") if it == current(data) else "Итерация закрыта",
        "phase_names": PHASES,
        "tickets_total": total,
        "tickets_by_state": by_state,
        "completion": round(by_state["done"] / total * 100) if total else 0,
        "req_total": len(reqs),
        "req_scored": len(scored),
        "req_confirmed": confirmed,
        "req_partial": sum(1 for r in scored if r.get("state") == "partial"),
        "req_missing": sum(1 for r in scored if r.get("state") == "missing"),
        "req_pending": pending,
        "req_out": len(reqs) - len(scored),
        # Попадание — только по сверенным требованиям: пока сверки не было,
        # число ничего не значит, и врать им нельзя.
        "hit": round(confirmed / (len(scored) - pending) * 100)
        if (len(scored) - pending) > 0 else None,
        "checks_total": len(checks),
        "checks_pass": sum(1 for c in checks if c.get("state") == "pass"),
        "minutes": {t["id"]: minutes(t) for t in tickets},
        "minutes_median": sorted(mins)[len(mins) // 2] if mins else None,
    }


def snapshot(root: pathlib.Path, data: dict) -> dict:
    """Всё, что нужно странице: метрики и волны по каждой итерации."""
    names = titles(root, data)
    reps = reports(root, data)
    per = {}
    for it in iterations(data):
        per[str(it)] = {
            "title": names.get(it),
            "metrics": metrics(data, it),
            "waves": waves(data, it),
            "report": reps.get(it),
        }
    return {"current": current(data), "iterations": per}
