"""Cliente SUNAT SIRE — OAuth + descarga de propuestas RCE/RVIE."""

from __future__ import annotations

import csv
import io
import time
import unicodedata
import zipfile
from typing import Any

import requests

AUTH_URL = "https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/"
SIRE_BASE = "https://api-sire.sunat.gob.pe"
SCOPE = "https://api-sire.sunat.gob.pe"

COD_LIBRO = {"compras": "080000", "ventas": "140000"}
EXPORT_URL = {
    "compras": (
        "/v1/contribuyente/migeigv/libros/rce/propuesta/web/propuesta/"
        "{periodo}/exportacioncomprobantepropuesta"
    ),
    "ventas": (
        "/v1/contribuyente/migeigv/libros/rvie/propuesta/web/propuesta/"
        "{periodo}/exportapropuesta"
    ),
}


class SunatError(Exception):
    pass


_REINTENTOS_429 = 4
_ESPERA_429 = (8, 15, 25, 40)


def _es_rate_limit(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    txt = (resp.text or "").lower()
    return "too many requests" in txt or "<title>429" in txt


def _sunat_request(method: str, url: str, **kwargs) -> requests.Response:
    ultimo: requests.Response | None = None
    for intento in range(_REINTENTOS_429):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise SunatError(f"No se pudo conectar con SUNAT: {exc}") from exc
        if not _es_rate_limit(resp):
            return resp
        ultimo = resp
        if intento < _REINTENTOS_429 - 1:
            time.sleep(_ESPERA_429[intento])
    assert ultimo is not None
    raise SunatError(
        "SUNAT limitó las consultas (429). Espera 1-2 minutos e intenta de nuevo."
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def obtener_token(
    ruc: str,
    usuario: str,
    clave: str,
    client_id: str,
    client_secret: str,
) -> str:
    if not client_id or not client_secret:
        raise SunatError(
            "Faltan client_id / client_secret. Regístralos en SUNAT → Credenciales API."
        )

    url = AUTH_URL.format(client_id=client_id)
    data = {
        "grant_type": "password",
        "scope": SCOPE,
        "client_id": client_id,
        "client_secret": client_secret,
        "username": f"{ruc}{usuario.upper()}",
        "password": clave,
    }

    r = _sunat_request("POST", url, data=data, timeout=60)

    if r.status_code != 200:
        detail = r.text[:300]
        raise SunatError(f"Autenticación SUNAT fallida ({r.status_code}): {detail}")

    payload = r.json()
    token = payload.get("access_token")
    if not token:
        raise SunatError("SUNAT no devolvió access_token")
    return token


def _parse_sunat_error(resp: requests.Response) -> str:
    if _es_rate_limit(resp):
        return "SUNAT limitó las consultas (429). Espera 1-2 minutos e intenta de nuevo."
    try:
        data = resp.json()
    except ValueError:
        txt = resp.text or ""
        if txt.strip().lower().startswith("<html"):
            return f"SUNAT respondió HTML ({resp.status_code}). Intenta de nuevo en unos minutos."
        return txt[:300]
    if isinstance(data, dict):
        if data.get("msg"):
            return str(data["msg"])
        errors = data.get("errors") or []
        if errors:
            return "; ".join(e.get("msg", str(e)) for e in errors)
    return resp.text[:300]


def solicitar_descarga(token: str, periodo: str, tipo: str) -> str:
    path = EXPORT_URL[tipo].format(periodo=periodo)
    params = {"codTipoArchivo": "1", "codOrigenEnvio": "2"} if tipo == "compras" else {"codTipoArchivo": "1"}
    url = f"{SIRE_BASE}{path}"

    r = _sunat_request("GET", url, headers=_headers(token), params=params, timeout=120)

    if r.status_code == 422:
        msg = _parse_sunat_error(r)
        if "1070" in msg or "1518" in msg or "No existen documentos" in msg or "No se ha encontrado" in msg:
            return ""
        raise SunatError(msg)

    if r.status_code != 200:
        raise SunatError(_parse_sunat_error(r))

    data = r.json()
    ticket = data.get("numTicket") or data.get("numticket")
    if not ticket:
        raise SunatError(f"SUNAT no devolvió numTicket para {tipo}")
    return str(ticket)


def consultar_ticket(
    token: str,
    periodo: str,
    num_ticket: str,
    cod_libro: str,
) -> dict[str, Any] | None:
    url = f"{SIRE_BASE}/v1/contribuyente/migeigv/libros/rvierce/gestionprocesosmasivos/web/masivo/consultaestadotickets"
    params = {
        "perIni": periodo,
        "perFin": periodo,
        "page": "1",
        "perPage": "20",
        "numTicket": num_ticket,
        "codLibro": cod_libro,
        "codOrigenEnvio": "2",
    }

    r = _sunat_request("GET", url, headers=_headers(token), params=params, timeout=60)
    if r.status_code != 200:
        raise SunatError(_parse_sunat_error(r))

    registros = (r.json().get("registros") or [])
    return registros[0] if registros else None


def _ticket_terminado(registro: dict[str, Any]) -> bool:
    estado = str(registro.get("codEstadoProceso", ""))
    desc = (registro.get("desEstadoProceso") or registro.get("desEstadoEnvio") or "").lower()
    if estado in {"3", "4", "03", "04"}:
        return True
    return "termin" in desc or "finaliz" in desc


def esperar_ticket(
    token: str,
    periodo: str,
    num_ticket: str,
    cod_libro: str,
    timeout: int = 180,
    intervalo: int = 3,
) -> dict[str, Any]:
    inicio = time.time()
    while time.time() - inicio < timeout:
        registro = consultar_ticket(token, periodo, num_ticket, cod_libro)
        if registro and _ticket_terminado(registro):
            return registro
        time.sleep(intervalo)
    raise SunatError(f"Tiempo agotado esperando ticket {num_ticket}")


def descargar_archivo_ticket(
    token: str,
    registro: dict[str, Any],
    cod_libro: str,
) -> bytes:
    archivos = registro.get("archivoReporte") or []
    if not archivos:
        nom = registro.get("nomArchivoReporte")
        if nom:
            archivos = [{"nomArchivoReporte": nom, "codTipoArchivoReporte": "01"}]
        else:
            det = registro.get("detalleTicket") or {}
            nom = det.get("nomArchivoReporte")
            if nom:
                archivos = [{"nomArchivoReporte": nom, "codTipoArchivoReporte": "01"}]

    if not archivos:
        raise SunatError("Ticket terminado pero sin archivoReporte")

    ar = archivos[0]
    params = {
        "nomArchivoReporte": ar.get("nomArchivoReporte"),
        "codTipoArchivoReporte": ar.get("codTipoArchivoReporte") or "01",
        "perTributario": registro.get("perTributario"),
        "codProceso": registro.get("codProceso"),
        "numTicket": (registro.get("detalleTicket") or {}).get("numTicket") or registro.get("numTicket"),
        "codLibro": cod_libro,
    }

    url = f"{SIRE_BASE}/v1/contribuyente/migeigv/libros/rvierce/gestionprocesosmasivos/web/masivo/archivoreporte"
    r = _sunat_request("GET", url, headers=_headers(token), params=params, timeout=180)
    if r.status_code != 200:
        raise SunatError(_parse_sunat_error(r))
    return r.content


def _leer_texto(raw: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_csv_text(text: str) -> list[dict[str, str]]:
    sample = text[:4096]
    delimiter = "|" if sample.count("|") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    filas: list[dict[str, str]] = []
    for row in reader:
        fila = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if any(fila.values()):
            filas.append(fila)
    return filas


_ALIASES: dict[str, list[str]] = {
    "Total CP": ["Total CP", "Importe total", "Monto total", "Total"],
    "IGV / IPM DG": ["IGV / IPM DG", "IGV IPM DG", "IGV / IPM", "IGV"],
    "IGV / IPM": ["IGV / IPM", "IGV IPM", "IGV"],
    "Valor Adq. NG": ["Valor Adq. NG", "Valor adquirido NG", "Valor Adq NG"],
    "Fecha de emisión": ["Fecha de emisión", "Fecha de emision", "Fecha emision"],
    "Apellidos Nombres/ Razón Social": [
        "Apellidos Nombres/ Razón Social",
        "Apellidos Nombres/ Razon Social",
        "Razón Social",
        "Razon Social",
    ],
    "Tipo CP/Doc.": [
        "Tipo CP/Doc.",
        "Tipo CP/Doc",
        "Tipo de Comprobante de Pago o Documento",
        "Tipo de comprobante de pago o documento",
        "Tipo de comprobante de Pago o Documento",
    ],
}


def _norm_key(key: str) -> str:
    text = unicodedata.normalize("NFD", key or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.lower().replace("/", " ").split())


def _valor_texto(val: str) -> str:
    v = (val or "").strip()
    if not v or v in {"-", "0", "00"}:
        return ""
    if v.isdigit() and len(v) <= 11:
        return ""
    return v


def _buscar_por_clave(fila: dict[str, str], incluir: tuple[str, ...], excluir: tuple[str, ...] = ()) -> str:
    for key, val in fila.items():
        nk = _norm_key(key)
        if any(x in nk for x in excluir):
            continue
        if all(x in nk for x in incluir):
            v = _valor_texto(val)
            if v:
                return v
    return ""


def _buscar_por_clave_raw(fila: dict[str, str], incluir: tuple[str, ...], excluir: tuple[str, ...] = ()) -> str:
    for key, val in fila.items():
        nk = _norm_key(key)
        if any(x in nk for x in excluir):
            continue
        if all(x in nk for x in incluir):
            v = (val or "").strip()
            if v:
                return v
    return ""


def _es_columna_declarante_compras(nk: str) -> bool:
    """Campo 2 RCE: nombre del declarante, no del proveedor/emisor."""
    return "nombres o razon" in nk or nk == "apellidos y nombres o razon social"


def _nombre_proveedor_compras(fila: dict[str, str]) -> str:
    """Campo 14 RCE: apellidos y razón social del emisor del comprobante."""
    explicitos = [
        "Apellidos Nombres/ Razón  Social",
        "Apellidos Nombres/ Razón Social",
        "Apellidos Nombres/ Razon Social",
        "Apellidos y nombres, denominación o razón social del emisor del comprobante de pago",
        "Apellidos y nombres, denominacion o razon social del emisor del comprobante de pago",
        "Apellidos y nombres, denominación o razón social del emisor",
    ]
    for nombre in explicitos:
        match = next((k for k in fila if _norm_key(k) == _norm_key(nombre)), None)
        if match:
            txt = _valor_texto(fila.get(match, ""))
            if txt:
                return txt

    for key, val in fila.items():
        nk = _norm_key(key)
        if _es_columna_declarante_compras(nk):
            continue
        if any(x in nk for x in ("cliente", "adquirente", "generador")):
            continue
        if ("apellidos" in nk and "nombres" in nk and "razon" in nk) or (
            "apellidos" in nk and "emisor" in nk
        ):
            txt = _valor_texto(val)
            if txt:
                return txt

    candidatos = [
        _buscar_por_clave(fila, ("apellidos", "emisor")),
        _buscar_por_clave(fila, ("razon", "social", "emisor")),
        _buscar_por_clave(fila, ("denominacion", "emisor")),
        _buscar_por_clave(fila, ("apellidos", "proveedor")),
        _buscar_por_clave(fila, ("razon", "social", "proveedor")),
    ]
    for val in candidatos:
        if val:
            return val
    return ""


def _nombre_tercero(fila: dict[str, str], tipo: str) -> str:
    if tipo == "compras":
        return _nombre_proveedor_compras(fila)

    candidatos = [
        _buscar_por_clave(fila, ("apellidos", "cliente")),
        _buscar_por_clave(fila, ("denominacion", "cliente")),
        _buscar_por_clave(fila, ("razon", "social", "cliente")),
        _buscar_por_clave(fila, ("apellidos", "denominacion")),
        _buscar_por_clave(fila, ("apellidos", "razon")),
        _buscar_por_clave(fila, ("nombre", "cliente")),
    ]
    for val in candidatos:
        if val:
            return val

    for canon, variants in _ALIASES.items():
        if "Apellidos" in canon or "Razón" in canon:
            for v in variants:
                match = next((k for k in fila if _norm_key(k) == _norm_key(v)), None)
                if match:
                    txt = _valor_texto(fila.get(match, ""))
                    if txt:
                        return txt
    return ""


def _ruc_proveedor(fila: dict[str, str]) -> str:
    """Campo 13 RCE: Nro Doc Identidad del emisor (proveedor)."""
    candidatos = [
        _buscar_por_clave_raw(fila, ("nro", "doc", "identidad"), ("cliente", "adquirente", "tipo")),
        _buscar_por_clave_raw(fila, ("numero", "doc", "identidad"), ("cliente", "adquirente", "tipo")),
        _buscar_por_clave_raw(fila, ("doc", "identidad", "emisor")),
        _buscar_por_clave_raw(fila, ("ruc", "emisor")),
        _buscar_por_clave_raw(fila, ("nro", "documento"), ("cliente", "adquirente")),
    ]
    for val in candidatos:
        digits = "".join(c for c in val if c.isdigit())
        if len(digits) >= 8:
            return digits[:11]
    return ""


def _top_proveedores_desde_filas(filas: list[dict[str, str]]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for row in filas:
        ruc = row.get("RUC Proveedor") or _ruc_proveedor(row)
        nombre = row.get("Nombre Proveedor") or _nombre_proveedor_compras(row) or "Desconocido"
        key = ruc or nombre
        try:
            monto = float(str(row.get("Total CP", "0")).replace(",", ""))
        except ValueError:
            monto = 0.0
        if key not in agg:
            agg[key] = {"nombre": nombre, "ruc": ruc, "monto": 0.0}
        if nombre != "Desconocido":
            agg[key]["nombre"] = nombre
        agg[key]["monto"] += monto
    top = sorted(agg.values(), key=lambda x: x["monto"], reverse=True)[:5]
    return [
        {
            "nombre": p["nombre"],
            "ruc": p["ruc"],
            "monto": p["monto"],
            "porcentaje": "",
        }
        for p in top
    ]


def _parse_reporte_proveedores(text: str) -> list[dict[str, Any]]:
    filas: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        partes = [p.strip() for p in line.split("|")]
        if len(partes) < 2:
            continue
        nombre = partes[0]
        if _norm_key(nombre) in {"razon social", "apellidos nombres razon social"}:
            continue
        monto_txt = partes[1].replace(" ", "").replace(",", "")
        try:
            monto = float(monto_txt)
        except ValueError:
            continue
        filas.append(
            {
                "nombre": nombre,
                "monto": monto,
                "porcentaje": partes[2] if len(partes) > 2 else "",
            }
        )
    filas.sort(key=lambda x: x["monto"], reverse=True)
    return filas[:5]


def _texto_desde_respuesta(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".txt", ".csv")):
                    return _leer_texto(zf.read(name))
        raise SunatError("ZIP de reporte proveedores sin TXT")
    return _leer_texto(content)


def descargar_top_proveedores(token: str, ruc: str, periodo: str) -> list[dict[str, Any]]:
    """Reporte oficial SUNAT 5.54 — montos por proveedor."""
    url = (
        f"{SIRE_BASE}/v1/contribuyente/migeigv/libros/rvierce/estadistica/web/"
        "resumenestadistico/exporta"
    )
    params = {
        "numRuc": ruc,
        "perTributario": periodo,
        "codTipoArchivo": "0",
        "codTipoReporte": "1",
        "codLibro": "080000",
    }
    r = _sunat_request("GET", url, headers=_headers(token), params=params, timeout=120)
    if r.status_code != 200:
        raise SunatError(_parse_sunat_error(r))

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" in ctype:
        data = r.json()
        ticket = data.get("numTicket") or data.get("numticket")
        if not ticket:
            raise SunatError("Reporte proveedores sin numTicket")
        registro = esperar_ticket(token, periodo, str(ticket), "080000")
        contenido = descargar_archivo_ticket(token, registro, "080000")
        text = _texto_desde_respuesta(contenido)
    else:
        text = _texto_desde_respuesta(r.content)

    proveedores = _parse_reporte_proveedores(text)
    if not proveedores:
        raise SunatError("Reporte proveedores vacío")
    return proveedores


def _normalizar_filas(filas: list[dict[str, str]], tipo: str = "") -> list[dict[str, str]]:
    if not filas:
        return filas
    keys = list(filas[0].keys())
    out: list[dict[str, str]] = []
    for fila in filas:
        row = dict(fila)
        for canon, variants in _ALIASES.items():
            if row.get(canon):
                continue
            for v in variants:
                match = next((k for k in keys if _norm_key(k) == _norm_key(v)), None)
                if match and row.get(match):
                    row[canon] = row[match]
                    break
        if tipo == "ventas":
            nombre = _nombre_tercero(row, "ventas")
            if nombre:
                row["Razón Social Cliente"] = nombre
        elif tipo == "compras":
            nombre = _nombre_tercero(row, "compras")
            if nombre:
                row["Nombre Proveedor"] = nombre
            ruc_prov = _ruc_proveedor(row)
            if ruc_prov:
                row["RUC Proveedor"] = ruc_prov
        out.append(row)
    return out


def parse_archivo(content: bytes, tipo: str = "") -> list[dict[str, str]]:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith((".csv", ".txt")):
                    return _normalizar_filas(_parse_csv_text(_leer_texto(zf.read(name))), tipo)
        raise SunatError("ZIP de SUNAT sin CSV/TXT legible")
    return _normalizar_filas(_parse_csv_text(_leer_texto(content)), tipo)


def descargar_propuesta(
    token: str,
    periodo: str,
    tipo: str,
) -> list[dict[str, str]]:
    cod_libro = COD_LIBRO[tipo]
    ticket = solicitar_descarga(token, periodo, tipo)
    if not ticket:
        return []

    registro = esperar_ticket(token, periodo, ticket, cod_libro)
    contenido = descargar_archivo_ticket(token, registro, cod_libro)
    return parse_archivo(contenido, tipo)


def consultar_periodo(
    ruc: str,
    usuario: str,
    clave: str,
    client_id: str,
    client_secret: str,
    periodo: str,
    token: str | None = None,
) -> dict[str, Any]:
    if not token:
        token = obtener_token(ruc, usuario, clave, client_id, client_secret)

    propuestas = []
    tipos = (("compras", "Compras (RCE)"), ("ventas", "Ventas (RVIE)"))
    for idx, (tipo, titulo) in enumerate(tipos):
        if idx > 0:
            time.sleep(2)
        try:
            filas = descargar_propuesta(token, periodo, tipo)
            propuestas.append(
                {
                    "tipo": tipo,
                    "titulo": titulo,
                    "filas": filas,
                    "notas": [f"Datos reales SUNAT · {len(filas)} registros"],
                }
            )
        except SunatError as exc:
            msg = str(exc)
            if "1070" in msg or "1518" in msg or "No existen documentos" in msg or "No se ha encontrado" in msg:
                propuestas.append(
                    {
                        "tipo": tipo,
                        "titulo": titulo,
                        "filas": [],
                        "notas": ["Sin registros en SUNAT para este período"],
                    }
                )
            else:
                propuestas.append(
                    {
                        "tipo": tipo,
                        "titulo": titulo,
                        "filas": [],
                        "error": msg,
                    }
                )

    top_proveedores: list[dict[str, Any]] = []
    compras = next((p["filas"] for p in propuestas if p["tipo"] == "compras"), [])
    if compras:
        top_proveedores = _top_proveedores_desde_filas(compras)

    return {
        "ok": True,
        "modo": "sunat",
        "periodo": periodo,
        "ruc": ruc,
        "propuestas": propuestas,
        "top_proveedores": top_proveedores,
    }
