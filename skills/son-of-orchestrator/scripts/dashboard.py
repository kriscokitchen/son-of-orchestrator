#!/usr/bin/env python3
"""Дашборд прогона: итерации, фаза, выполнение, попадание, волны.

Читает .orchestrator/status.json, считает всё в state.py, подставляет в
dashboard-template.html и пишет .orchestrator/dashboard.html.

    python3 dashboard.py [путь-к-проекту]                 перерисовать один раз
    python3 dashboard.py [путь-к-проекту] --serve [--port N]
                                                          сервер и вкладка; страница
                                                          обновляется сама

Снимок данных вшивается в страницу всегда — чтобы файл открывался двойным
щелчком и без сервера. Под сервером страница раз в две секунды берёт
/state.json: сервер считает его из status.json на каждый запрос тем же кодом,
что и снимок, поэтому страница под сервером и файл без него не расходятся.
"""
from __future__ import annotations

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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402


def payload(root: pathlib.Path) -> dict:
    data = state.load(root)
    return {"status": data, "snap": state.snapshot(root, data),
            "rendered": datetime.now().astimezone().isoformat(timespec="seconds")}


def render(root: pathlib.Path, template: pathlib.Path) -> str:
    html = template.read_text(encoding="utf-8")
    if "__DATA__" not in html:
        sys.exit(f"В шаблоне {template} нет метки __DATA__.")
    # </script> внутри строк данных закрыл бы тег раньше времени
    data = json.dumps(payload(root), ensure_ascii=False).replace("</", "<\\/")
    return html.replace("__DATA__", data)


def serve(root: pathlib.Path, port: int) -> None:
    directory = str(state.orch(root))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=directory, **kw)

        def do_GET(self):
            if self.path.split("?")[0] == "/state.json":
                try:
                    body = json.dumps(payload(root), ensure_ascii=False).encode("utf-8")
                except SystemExit as exc:  # status.json на полпути записи — отдадим прошлый снимок
                    self.send_error(503, str(exc))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def end_headers(self):
            # Иначе браузер отдаёт данные из кеша и прогресс замирает.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

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
    ap.add_argument("--out", help="куда писать страницу (по умолчанию .orchestrator/dashboard.html)")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    template = pathlib.Path(__file__).with_name("dashboard-template.html")
    if not template.exists():
        sys.exit(f"Нет шаблона {template}.")
    out = pathlib.Path(args.out) if args.out else state.orch(root) / "dashboard.html"
    out.write_text(render(root, template), encoding="utf-8")
    print(out)

    snap = payload(root)["snap"]
    cur = snap["current"]
    for k in sorted(snap["iterations"], key=int):
        v = snap["iterations"][k]
        m = v["metrics"]
        hit = "сверки нет" if m["hit"] is None else f"попадание {m['hit']}%"
        mark = "  ← текущая" if int(k) == cur else ""
        print(f"итерация {k}: {m['tickets_by_state']['done']} из {m['tickets_total']}, {hit}{mark}")
    ready = snap["iterations"][str(cur)]["waves"]["ready"]["now"]
    print("можно запустить сейчас: " + (", ".join(ready) or "ничего"))

    if args.serve:
        if args.out:
            sys.exit("--serve работает со страницей в .orchestrator/, без --out.")
        serve(root, args.port)


if __name__ == "__main__":
    main()
