#!/usr/bin/env python3
"""Дашборд прогона: фаза, выполнение и попадание.

Читает .orchestrator/status.json, считает метрики, подставляет их в
dashboard-template.html и пишет .orchestrator/dashboard.html.

Два режима:

    python3 dashboard.py                 перерисовать страницу один раз
    python3 dashboard.py --serve         поднять локальный сервер и открыть
                                         браузер; страница сама опрашивает
                                         status.json и обновляется на ходу

Снимок данных вшивается в страницу всегда — чтобы файл открывался двойным
щелчком и без сервера. Под сервером страница поверх снимка опрашивает
status.json: тикеты идут в разных окнах, и каждое пишет в тот же файл.

    python3 dashboard.py [--serve] [--port N] [путь-к-проекту]
"""
import argparse
import http.server
import json
import pathlib
import socket
import socketserver
import sys
import threading
import webbrowser
from datetime import datetime

TICKET_DONE = "done"
TICKET_STATES = ("todo", "doing", "done", "failed")
REQ_STATES = ("pending", "confirmed", "partial", "missing", "out-of-scope")


def load(root: pathlib.Path) -> dict:
    path = root / ".orchestrator" / "status.json"
    if not path.exists():
        sys.exit(f"Нет {path}. Дашборд рисуется после Фазы 4.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"{path} повреждён: {exc}")


PHASES = [
    "Проверка окружения",
    "Вопросы",
    "Спека",
    "Направление",
    "Задачи",
    "Раздача и сборка",
    "Финиш",
]


def metrics(data: dict) -> dict:
    tickets = data.get("tickets", [])
    reqs = data.get("requirements", [])

    by_state = {s: sum(1 for t in tickets if t.get("state") == s) for s in TICKET_STATES}
    done = by_state[TICKET_DONE]
    total = len(tickets)

    scored = [r for r in reqs if r.get("state") != "out-of-scope"]
    confirmed = sum(1 for r in scored if r.get("state") == "confirmed")
    partial = sum(1 for r in scored if r.get("state") == "partial")
    missing = sum(1 for r in scored if r.get("state") == "missing")
    pending = sum(1 for r in scored if r.get("state") == "pending")

    checks = [c for t in tickets for c in t.get("checks", [])]

    phase = data.get("phase", {})
    current = phase.get("current")

    return {
        "phase_current": current,
        "phase_name": phase.get("name") or (PHASES[current] if isinstance(current, int) and 0 <= current < len(PHASES) else None),
        "phase_names": PHASES,
        "tickets_total": total,
        "tickets_by_state": by_state,
        "completion": round(done / total * 100) if total else 0,
        "req_total": len(reqs),
        "req_scored": len(scored),
        "req_confirmed": confirmed,
        "req_partial": partial,
        "req_missing": missing,
        "req_pending": pending,
        "req_out": len(reqs) - len(scored),
        # Попадание считается только по сверенным требованиям: пока сверки не
        # было, число ничего не значит, и врать им нельзя.
        "hit": round(confirmed / (len(scored) - pending) * 100)
        if (len(scored) - pending) > 0 else None,
        "checks_total": len(checks),
        "checks_pass": sum(1 for c in checks if c.get("state") == "pass"),
    }


def render(data: dict, m: dict, template: pathlib.Path) -> str:
    html = template.read_text(encoding="utf-8")
    payload = json.dumps({"status": data, "metrics": m,
                          "rendered": datetime.now().isoformat(timespec="seconds")},
                         ensure_ascii=False)
    if "__DATA__" not in html:
        sys.exit(f"В шаблоне {template} нет метки __DATA__.")
    return html.replace("__DATA__", payload)


def serve(root: pathlib.Path, port: int) -> None:
    """Локальный сервер над .orchestrator/, чтобы страница опрашивала status.json.

    На file:// запрос к соседнему файлу блокируется правилом одного источника,
    поэтому живое обновление возможно только через http.
    """
    directory = str(root / ".orchestrator")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=directory, **kw)

        def end_headers(self):
            # Иначе браузер отдаёт status.json из кеша и прогресс замирает.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    if port == 0:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

    with Server(("127.0.0.1", port), Handler) as httpd:
        url = f"http://127.0.0.1:{port}/dashboard.html"
        print(f"Дашборд: {url}")
        print("Обновляется сам, пока это окно открыто. Остановить — Ctrl+C.")
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nСервер остановлен. Страница осталась на диске.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Дашборд прогона son-of-orchestrator")
    ap.add_argument("root", nargs="?", default=".", help="корень проекта")
    ap.add_argument("--serve", action="store_true", help="поднять сервер и открыть браузер")
    ap.add_argument("--port", type=int, default=0, help="порт (по умолчанию свободный)")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    data = load(root)
    m = metrics(data)
    template = pathlib.Path(__file__).with_name("dashboard-template.html")
    if not template.exists():
        sys.exit(f"Нет шаблона {template}.")
    out = root / ".orchestrator" / "dashboard.html"
    out.write_text(render(data, m, template), encoding="utf-8")

    hit = "—" if m["hit"] is None else f"{m['hit']}%"
    phase = m["phase_name"] or "не начата"
    print(f"{out}")
    print(f"фаза       {phase}")
    print(f"выполнение {m['completion']}%  ({m['tickets_by_state']['done']} из {m['tickets_total']})")
    print(f"попадание  {hit}"
          + ("  (сверка на финише ещё не проводилась)" if m["hit"] is None else ""))

    if args.serve:
        serve(root, args.port)


if __name__ == "__main__":
    main()
