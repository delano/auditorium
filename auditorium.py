"""Brenda CLI and same-origin loopback HTTP surface."""

import argparse
import base64
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import core


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, kind="application/json"):
        raw = json.dumps(body).encode() if kind == "application/json" else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(raw)

    def allowed_host(self):
        return self.headers.get("Host") in (
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        )

    def do_GET(self):
        try:
            core.require(self.allowed_host(), "Use the local viewer address.")
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path == "/api/bootstrap":
                return self.send(200, {"database": self.server.database})
            if url.path in ("/api/state", "/api/image"):
                database = query["database"][0]
                if url.path == "/api/state":
                    return self.send(200, core.state(database))
                with core.connection(database) as conn:
                    row = conn.execute(
                        "SELECT content,mime_type FROM evidence WHERE id=?",
                        (int(query["id"][0]),),
                    ).fetchone()
                    core.require(row, "Image not found.")
                    return self.send(200, row["content"], row["mime_type"])
            assets = {
                "/": ("ui.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            core.require(url.path in assets, "Page not found.")
            file, kind = assets[url.path]
            self.send(200, (core.HERE / file).read_bytes(), kind)
        except (ValueError, KeyError, TypeError, sqlite3.Error, OSError) as exc:
            self.send(400, {"error": str(exc)})

    def do_POST(self):
        try:
            core.require(self.allowed_host(), "Use the local viewer address.")
            core.require(
                self.headers.get("Origin") == "http://" + self.headers.get("Host", ""),
                "Writes require the same-origin viewer.",
            )
            core.require(self.path == "/api/action", "Unknown endpoint.")
            length = int(self.headers.get("Content-Length", "0"))
            core.require(0 < length <= 30 * 1024 * 1024, "Request size is invalid.")
            payload = json.loads(self.rfile.read(length))
            core.require(isinstance(payload, dict), "Use a brenda action object.")
            database = unquote(self.headers["X-Auditorium-Database"])
            with core.connection(database) as conn:
                ws = dict(conn.execute("SELECT * FROM workspace").fetchone())
                core.require(
                    payload.get("database_id") == ws["id"],
                    "Database identity changed. Reload the selected file.",
                )
            name = payload["action"]
            if name == "browse":
                script = 'POSIX path of (choose file with prompt "Load an Auditorium SQLite file")'
                if payload.get("operation") == "create":
                    script = 'POSIX path of (choose file name with prompt "Create an Auditorium SQLite file" default name "auditorium-brenda.sqlite")'
                try:
                    path = core.run(["osascript", "-e", script])
                except ValueError as exc:
                    if "-128" in str(exc):
                        return self.send(200, {"cancelled": True})
                    raise
                return self.send(200, {"path": path})
            if name == "load":
                return self.send(200, {"state": core.state(payload["path"])})
            if name == "create":
                path = core.create(payload["path"], ws["repository"], ws["github"])
                return self.send(200, {"state": core.state(path)})
            if name == "own_check":
                # One transaction records a human check and optional acceptance.
                with core.connection(database, True) as conn:
                    hid = core.make_handoff(
                        conn,
                        database,
                        "validation",
                        payload["batch_id"],
                        [payload["check"]["finding_id"]],
                    )
                    report = {
                        "contract": "brenda",
                        "database_id": ws["id"],
                        "handoff_id": hid,
                        "checks": [payload["check"]],
                    }
                    core.record(database, report, operator=True, transaction=conn)
                    if payload.get("accept"):
                        item = next(
                            i
                            for i in core.queue(conn, payload["batch_id"])
                            if i["id"] == payload["check"]["finding_id"]
                        )
                        core.decide(
                            conn,
                            payload["batch_id"],
                            [
                                {
                                    "finding_id": item["id"],
                                    "check_id": item["check"]["id"],
                                    "decision_id": item["decision"]["id"]
                                    if item["decision"]
                                    else None,
                                }
                            ],
                            "accept",
                            "",
                        )
                return self.send(200, {"state": core.state(database)})
            result = core.action(database, payload)
            return self.send(200, {"state": core.state(database), "handoff_id": result})
        except (
            ValueError,
            KeyError,
            TypeError,
            sqlite3.Error,
            OSError,
            StopIteration,
        ) as exc:
            self.send(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "serve", "record", "schema"):
        p = sub.add_parser(name)
        p.add_argument("--database", required=True)
        if name == "create":
            p.add_argument("--repository", required=True)
            p.add_argument("--github", required=True)
        if name == "serve":
            p.add_argument("--port", type=int, default=8767)
        if name == "record":
            p.add_argument("--report", required=True)
    args = parser.parse_args()
    if args.command == "create":
        print(core.create(args.database, args.repository, args.github))
    elif args.command == "record":
        report = json.loads(Path(args.report).read_text())
        for check in report.get("checks", []):
            for image in check.get("screenshots", []):
                image["content"] = base64.b64encode(
                    Path(image.pop("path")).read_bytes()
                ).decode()
        core.record(args.database, report)
        print("Results recorded; operator decisions remain unchanged.")
    elif args.command == "schema":
        print("\n\n".join(r["sql"] for r in core.state(args.database)["schema"]))
    else:
        core.state(args.database)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.database = str(Path(args.database).resolve())
        print(f"Auditorium {core.VERSION}: http://127.0.0.1:{args.port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
