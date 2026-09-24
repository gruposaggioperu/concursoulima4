#!/usr/bin/env python3
"""Servidor local Control de Impuestos — frontend + API SUNAT SIRE real."""

from __future__ import annotations

import json
import secrets
import sys
import time
from datetime import date
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bcrp_indicadores import (
    calcular_inactividad,
    obtener_indicadores_bcrp,
    obtener_ipc,
    obtener_tasa_referencia,
    obtener_tipo_cambio,
)
from sunat_sire import SunatError, consultar_periodo, obtener_token

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
UIT_PATH = ROOT / "data" / "uit_historico.json"
SERVER_VERSION = "2.1.0"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

# Sesiones en memoria: session_id -> credenciales SUNAT
SESIONES: dict[str, dict] = {}


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def parse_query(path: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlparse(path)
    params = urllib.parse.parse_qs(parsed.query)
    flat = {k: v[0] if v else "" for k, v in params.items()}
    return parsed.path, flat


class SireHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    @property
    def config(self) -> dict:
        return load_config()

    def do_GET(self) -> None:
        path, params = parse_query(self.path)

        if path == "/api/version":
            return json_response(
                self,
                200,
                {
                    "ok": True,
                    "version": SERVER_VERSION,
                    "features": ["indicadores", "uit", "inactividad", "tasa_referencia", "ipc"],
                },
            )

        if path == "/api/config":
            public = {k: v for k, v in self.config.items() if k != "clientes_prueba"}
            public["server_version"] = SERVER_VERSION
            return json_response(self, 200, public)

        if path == "/api/clientes":
            clientes = self.config.get("clientes_prueba", [])
            lista = [
                {
                    "nombre": c.get("nombre", ""),
                    "ruc": c.get("ruc", ""),
                    "usuario": c.get("usuario", ""),
                    "clave": c.get("clave", ""),
                    "empresa_id": c.get("empresa_id", ""),
                    "nota": c.get("nota", ""),
                    "tiene_api": bool(c.get("client_id") and c.get("client_secret")),
                }
                for c in clientes
            ]
            return json_response(self, 200, {"ok": True, "clientes": lista})

        if path == "/api/login":
            return self.handle_login(params)

        if path == "/api/consulta":
            return self.handle_consulta(params)

        if path == "/api/tipocambio":
            return self.handle_tipocambio()

        if path == "/api/indicadores":
            return self.handle_indicadores(params)

        if path == "/api/uit":
            return self.handle_uit(params)

        if path == "/api/tasa-referencia":
            return self.handle_tasa_referencia()

        if path == "/api/ipc":
            return self.handle_ipc()

        if path in ("/", ""):
            return self.serve_file(ROOT / "index.html")

        rel = path.lstrip("/")
        target = (ROOT / rel).resolve()
        if not str(target).startswith(str(ROOT.resolve())):
            self.send_error(403, "Forbidden")
            return
        if target.is_file():
            return self.serve_file(target)

        self.send_error(404, "Not found")

    def find_cliente(self, ruc: str, usuario: str, clave: str) -> dict | None:
        for c in self.config.get("clientes_prueba", []):
            cfg_clave = c.get("clave", "").strip()
            if (
                c.get("ruc", "").strip() == ruc
                and c.get("usuario", "").strip().upper() == usuario.upper()
                and (not cfg_clave or cfg_clave == clave)
            ):
                merged = dict(c)
                if clave:
                    merged["clave"] = clave
                return merged
        return None

    def handle_login(self, params: dict[str, str]) -> None:
        ruc = params.get("ruc", "").strip()
        usuario = params.get("usuario", "").strip()
        clave = params.get("clave", "").strip()

        if not ruc or not usuario or not clave:
            return json_response(
                self, 400, {"ok": False, "error": "Ingresa RUC, usuario y clave SOL"}
            )

        if len(ruc) != 11 or not ruc.isdigit():
            return json_response(self, 400, {"ok": False, "error": "RUC inválido (11 dígitos)"})

        cliente = self.find_cliente(ruc, usuario, clave)
        if not cliente and self.config.get("login", {}).get("modo_dev"):
            cliente = {
                "nombre": f"Contribuyente {ruc}",
                "ruc": ruc,
                "usuario": usuario,
                "clave": clave,
                "client_id": "",
                "client_secret": "",
                "empresa_id": self.config.get("empresa", {}).get("id", ""),
            }
        elif not cliente:
            return json_response(
                self,
                401,
                {"ok": False, "error": "Credenciales no reconocidas. Selecciona un cliente de prueba."},
            )

        client_id = (cliente.get("client_id") or "").strip()
        client_secret = (cliente.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return json_response(
                self,
                400,
                {
                    "ok": False,
                    "error": "Faltan credenciales API SUNAT (client_id/client_secret) en config.json",
                },
            )

        try:
            token = obtener_token(ruc, usuario, clave, client_id, client_secret)
        except SunatError as exc:
            return json_response(self, 401, {"ok": False, "error": str(exc)})

        session_id = secrets.token_urlsafe(24)
        SESIONES[session_id] = {
            "ruc": ruc,
            "usuario": usuario,
            "clave": clave,
            "client_id": client_id,
            "client_secret": client_secret,
            "nombre": cliente.get("nombre", f"Contribuyente {ruc}"),
            "empresa_id": cliente.get("empresa_id", ""),
            "sunat_token": token,
            "sunat_token_ts": time.time(),
        }

        return json_response(
            self,
            200,
            {
                "ok": True,
                "session_id": session_id,
                "nombre": SESIONES[session_id]["nombre"],
                "ruc": ruc,
                "usuario": usuario,
                "empresa_id": SESIONES[session_id]["empresa_id"],
            },
        )

    def handle_tipocambio(self) -> None:
        try:
            data = obtener_tipo_cambio()
            return json_response(self, 200, {"ok": True, **data})
        except Exception as exc:
            return json_response(
                self,
                502,
                {"ok": False, "error": f"No se pudo obtener tipo de cambio BCR: {exc}"},
            )

    def handle_tasa_referencia(self) -> None:
        try:
            data = obtener_tasa_referencia()
            return json_response(self, 200, {"ok": True, **data})
        except Exception as exc:
            return json_response(self, 502, {"ok": False, "error": str(exc)})

    def handle_ipc(self) -> None:
        try:
            data = obtener_ipc()
            return json_response(self, 200, {"ok": True, **data})
        except Exception as exc:
            return json_response(self, 502, {"ok": False, "error": str(exc)})

    def load_uit_table(self) -> dict:
        if not UIT_PATH.is_file():
            raise FileNotFoundError("Falta data/uit_historico.json")
        return json.loads(UIT_PATH.read_text(encoding="utf-8"))

    def handle_uit(self, params: dict[str, str]) -> None:
        try:
            tabla = self.load_uit_table()
            valores = tabla.get("valores") or {}
            anio = params.get("anio", "").strip()
            if anio:
                if anio not in valores:
                    return json_response(
                        self,
                        404,
                        {"ok": False, "error": f"UIT no disponible para el año {anio}"},
                    )
                item = valores[anio]
                return json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "anio": int(anio),
                        "uit": item["uit"],
                        "base_legal": item.get("base_legal", ""),
                        "fuente": tabla.get("fuente", ""),
                        "url": tabla.get("url", ""),
                    },
                )
            return json_response(
                self,
                200,
                {
                    "ok": True,
                    "fuente": tabla.get("fuente", ""),
                    "url": tabla.get("url", ""),
                    "valores": valores,
                },
            )
        except Exception as exc:
            return json_response(self, 502, {"ok": False, "error": str(exc)})

    def handle_indicadores(self, params: dict[str, str]) -> None:
        anio = params.get("anio", "").strip() or str(date.today().year)
        payload: dict = {"ok": True, "anio": int(anio)}
        errores: list[str] = []

        try:
            payload["tipo_cambio"] = obtener_tipo_cambio()
        except Exception as exc:
            errores.append(f"TC: {exc}")

        try:
            payload["tasa_referencia"] = obtener_tasa_referencia()
        except Exception as exc:
            errores.append(f"Tasa ref.: {exc}")

        try:
            payload["ipc"] = obtener_ipc()
        except Exception as exc:
            errores.append(f"IPC: {exc}")

        try:
            tabla = self.load_uit_table()
            valores = tabla.get("valores") or {}
            if anio in valores:
                item = valores[anio]
                payload["uit"] = {
                    "valor": item["uit"],
                    "anio": int(anio),
                    "base_legal": item.get("base_legal", ""),
                    "fuente": tabla.get("fuente", ""),
                    "url": tabla.get("url", ""),
                }
            else:
                errores.append(f"UIT: sin valor para {anio}")
        except Exception as exc:
            errores.append(f"UIT: {exc}")

        payload["series_bcrp"] = self.config.get("series_bcrp", {})
        if errores:
            payload["avisos"] = errores
        status = 200 if any(k in payload for k in ("tipo_cambio", "tasa_referencia", "ipc", "uit")) else 502
        if status == 502:
            payload["ok"] = False
            payload["error"] = "; ".join(errores)
        return json_response(self, status, payload)

    def handle_consulta(self, params: dict[str, str]) -> None:
        session_id = params.get("session_id", "").strip()
        periodo = params.get("periodo", "").strip()

        if not session_id or session_id not in SESIONES:
            return json_response(
                self, 401, {"ok": False, "error": "Sesión inválida. Vuelve a ingresar."}
            )

        if not periodo or len(periodo) != 6 or not periodo.isdigit():
            return json_response(self, 400, {"ok": False, "error": "Período inválido. Usa YYYYMM"})

        ses = SESIONES[session_id]
        try:
            token = ses.get("sunat_token")
            token_ts = float(ses.get("sunat_token_ts") or 0)
            if not token or (time.time() - token_ts) > 3300:
                token = obtener_token(
                    ses["ruc"],
                    ses["usuario"],
                    ses["clave"],
                    ses["client_id"],
                    ses["client_secret"],
                )
                ses["sunat_token"] = token
                ses["sunat_token_ts"] = time.time()

            data = consultar_periodo(
                ruc=ses["ruc"],
                usuario=ses["usuario"],
                clave=ses["clave"],
                client_id=ses["client_id"],
                client_secret=ses["client_secret"],
                periodo=periodo,
                token=token,
            )
            data["empresa_id"] = ses.get("empresa_id", "")
            try:
                ventas = next(
                    (p.get("filas") or [] for p in data.get("propuestas", []) if p.get("tipo") == "ventas"),
                    [],
                )
                umbral = int(self.config.get("alerta_inactividad", {}).get("dias_habiles_umbral", 5))
                data["inactividad"] = calcular_inactividad(ventas, umbral, periodo)
            except Exception as exc:
                data["inactividad"] = {"alerta": False, "mensaje": f"No se pudo calcular inactividad: {exc}"}
            return json_response(self, 200, data)
        except SunatError as exc:
            return json_response(self, 502, {"ok": False, "error": str(exc)})

    def serve_file(self, path: Path) -> None:
        suffix = path.suffix.lower()
        content_type = MIME.get(suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    try:
        import requests  # noqa: F401
    except ImportError:
        print("Instala dependencias: pip install -r requirements.txt")
        sys.exit(1)

    config = load_config()
    import os
    port = int(os.environ.get("PORT") or config.get("servidor", {}).get("puerto", 8080))
    host = os.environ.get("HOST", "127.0.0.1")

    try:
        server = ThreadingHTTPServer((host, port), SireHandler)
    except OSError as exc:
        print("=" * 56)
        print(f"  ERROR: Puerto {port} ya está en uso.")
        print("  Cierra el servidor anterior y vuelve a ejecutar iniciar.bat")
        print(f"  Detalle: {exc}")
        print("=" * 56)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    print("=" * 56)
    print("  Control de Impuestos — SUNAT SIRE (real)")
    print("=" * 56)
    print(f"  URL:      {url}")
    print(f"  Versión:  {SERVER_VERSION}")
    print(f"  Modo:     {config.get('servidor', {}).get('modo', 'sunat')}")
    print(f"  Clientes: {len(config.get('clientes_prueba', []))}")
    print("  Ctrl+C para detener")
    print("=" * 56)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
