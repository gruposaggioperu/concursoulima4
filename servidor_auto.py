#!/usr/bin/env python3
"""Supervisor del servidor Control de Impuestos — mantiene el servicio activo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / ".servidor.lock"
LOG = ROOT / "servidor.log"
PAUSA_REINICIO = 5


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def puerto() -> int:
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return int(cfg.get("servidor", {}).get("puerto", 8080))
    except Exception:
        return 8080


def servidor_activo(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=3) as r:
            data = json.loads(r.read())
        return bool(data.get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False


def pid_supervisor_activo() -> int | None:
    if not LOCK.is_file():
        return None
    try:
        pid = int(LOCK.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def escribir_lock() -> None:
    LOCK.write_text(str(os.getpid()), encoding="utf-8")


def quitar_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def instalar_deps() -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"],
        cwd=ROOT,
        check=False,
    )


def abrir_navegador(port: int) -> None:
    try:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    except Exception:
        pass


def main() -> None:
    port = puerto()
    otro = pid_supervisor_activo()
    if otro and otro != os.getpid():
        log(f"Supervisor ya en ejecución (PID {otro}).")
        if servidor_activo(port):
            log(f"Servidor OK en http://127.0.0.1:{port}/")
            abrir_navegador(port)
        return

    if servidor_activo(port):
        log(f"El servidor ya responde en http://127.0.0.1:{port}/")
        abrir_navegador(port)
        return

    escribir_lock()
    instalar_deps()
    log("Supervisor iniciado. Ctrl+C para detener.")
    log(f"URL: http://127.0.0.1:{port}/")

    abrir_navegador(port)
    proc: subprocess.Popen | None = None

    try:
        while True:
            log("Arrancando server.py...")
            proc = subprocess.Popen(
                [sys.executable, "server.py"],
                cwd=ROOT,
            )
            code = proc.wait()
            if code == 0:
                log("Servidor detenido normalmente.")
                break
            log(f"Servidor terminó con código {code}. Reinicio en {PAUSA_REINICIO}s...")
            time.sleep(PAUSA_REINICIO)
    except KeyboardInterrupt:
        log("Deteniendo supervisor...")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        quitar_lock()
        log("Supervisor finalizado.")


if __name__ == "__main__":
    main()
