#!/usr/bin/env python3

import os
import socket
import sys
import time
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from waitress.server import create_server


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def read_web_settings():
    defaults = {"webserver_enable": "1", "webserver_http_port": "80"}
    if not all([DB_HOST, DB_USER, DB_NAME]):
        return defaults
    try:
        conn = db()
    except Exception as exc:
        print(f"webd database connection failed, using defaults: {exc}", flush=True)
        return defaults
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT parameter, value FROM systemsettings WHERE parameter IN ('webserver_enable','webserver_http_port')")
            for row in cur.fetchall():
                defaults[str(row["parameter"])] = str(row["value"])
    finally:
        conn.close()
    return defaults


def enabled(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def port_value(value):
    try:
        port = int(str(value or "").strip())
    except ValueError:
        return 80
    if not 1 <= port <= 65535:
        return 80
    return port


def ports_to_try(configured_port):
    configured = port_value(configured_port)
    ports = [configured]
    if configured != 80:
        ports.append(80)
    return ports


class StripServerHeaderMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        def filtered_start_response(status, headers, exc_info=None):
            filtered = [(name, value) for name, value in headers if name.lower() != "server"]
            return start_response(status, filtered, exc_info)

        return self.app(environ, filtered_start_response)


def create_waitress_server(app, port):
    return create_server(StripServerHeaderMiddleware(app), host="0.0.0.0", port=port, ident="")


def main():
    settings = read_web_settings()
    if not enabled(settings.get("webserver_enable")):
        print("webd disabled by webserver_enable=0", flush=True)
        return 0

    from web.app import app

    ports = ports_to_try(settings.get("webserver_http_port"))
    last_error = None
    while True:
        for port in ports:
            try:
                server = create_waitress_server(app, port)
            except (OSError, socket.error) as exc:
                last_error = exc
                print(f"webd port {port} unavailable: {exc}", flush=True)
                continue
            print(f"http://0.0.0.0:{port}", flush=True)
            server.run()
            return 0
        print(f"waiting for ports {', '.join(map(str, ports))} to become available; last error: {last_error}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
