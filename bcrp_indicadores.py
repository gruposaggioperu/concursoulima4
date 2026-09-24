"""Consulta series estadísticas del BCRP (estadisticas.bcrp.gob.pe)."""

from __future__ import annotations

import re
from typing import Any

import requests

BCRP_API = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"

# Códigos verificados en el portal BCRP (sep. 2026)
SERIES = {
    "tipo_cambio_compra": "PD04637PD",
    "tipo_cambio_venta": "PD04638PD",
    "expectativa_tc_12m": "PD38049AM",
    "tasa_referencia": "PD12301MD",
    "ipc_mensual": "PN01271PM",
    "ipc_interanual": "PN01273PM",
}


def _valor_valido(raw: Any) -> bool:
    if raw is None:
        return False
    s = str(raw).strip()
    return s not in ("", "null", "None", "nan")


def _ultimo_periodo(data: dict[str, Any], indice: int = 0) -> tuple[str, float] | None:
    for periodo in reversed(data.get("periods") or []):
        valores = periodo.get("values") or []
        if len(valores) <= indice:
            continue
        raw = valores[indice]
        if not _valor_valido(raw):
            continue
        try:
            return str(periodo.get("name", "")), float(str(raw).replace(",", ""))
        except ValueError:
            continue
    return None


def consultar_series(codigos: list[str], timeout: int = 25) -> dict[str, Any]:
    if not codigos:
        raise ValueError("Se requiere al menos un código de serie BCRP")
    url = f"{BCRP_API}/{'-'.join(codigos)}/json/"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def obtener_expectativa_tc_12m() -> dict[str, Any]:
    data = consultar_series([SERIES["expectativa_tc_12m"]])
    ultimo = _ultimo_periodo(data, 0)
    if not ultimo:
        raise ValueError("Sin datos de expectativa de TC BCRP")
    return {
        "valor": round(ultimo[1], 4),
        "fecha": ultimo[0],
        "serie": SERIES["expectativa_tc_12m"],
        "fuente": "BCRP — Encuesta de Expectativas (TC a 12 meses)",
    }


def obtener_tipo_cambio() -> dict[str, Any]:
    data = consultar_series([SERIES["tipo_cambio_compra"], SERIES["tipo_cambio_venta"]])
    compra = _ultimo_periodo(data, 0)
    venta = _ultimo_periodo(data, 1)
    if not compra or not venta:
        raise ValueError("Sin datos de tipo de cambio BCRP")
    fecha = compra[0] if compra[0] == venta[0] else f"{compra[0]} / {venta[0]}"
    result: dict[str, Any] = {
        "compra": round(compra[1], 4),
        "venta": round(venta[1], 4),
        "fecha": fecha,
        "series": [SERIES["tipo_cambio_compra"], SERIES["tipo_cambio_venta"]],
        "fuente": "BCRP — TC Interbancario",
    }
    try:
        result["expectativa_12m"] = obtener_expectativa_tc_12m()
    except Exception:
        pass
    return result


def obtener_tasa_referencia() -> dict[str, Any]:
    data = consultar_series([SERIES["tasa_referencia"]])
    ultimo = _ultimo_periodo(data, 0)
    if not ultimo:
        raise ValueError("Sin datos de tasa de referencia BCRP")
    return {
        "valor": round(ultimo[1], 2),
        "fecha": ultimo[0],
        "serie": SERIES["tasa_referencia"],
        "fuente": "BCRP — Tasa de Referencia de la Política Monetaria",
    }


def obtener_ipc() -> dict[str, Any]:
    data = consultar_series([SERIES["ipc_mensual"], SERIES["ipc_interanual"]])
    mensual = _ultimo_periodo(data, 0)
    interanual = _ultimo_periodo(data, 1)
    if not mensual or not interanual:
        raise ValueError("Sin datos de IPC BCRP")
    return {
        "mensual": round(mensual[1], 2),
        "interanual": round(interanual[1], 2),
        "fecha": mensual[0],
        "series": {
            "mensual": SERIES["ipc_mensual"],
            "interanual": SERIES["ipc_interanual"],
        },
        "fuente": "BCRP — IPC Lima Metropolitana",
    }


def obtener_indicadores_bcrp() -> dict[str, Any]:
    tc = obtener_tipo_cambio()
    tasa = obtener_tasa_referencia()
    ipc = obtener_ipc()
    return {"tipo_cambio": tc, "tasa_referencia": tasa, "ipc": ipc}


def parse_fecha_sunat(texto: str) -> tuple[int, int, int] | None:
    """Parsea fechas SUNAT: DD/MM/YYYY, DD-MM-YYYY, YYYYMMDD, DDMMYYYY."""
    t = (texto or "").strip()
    if not t or t in {"-", "0", "00"}:
        return None

    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return y, mo, d

    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return y, mo, d

    m = re.match(r"^(\d{2})(\d{2})(\d{4})$", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return y, mo, d
    return None


def _fecha_emision_fila(fila: dict[str, str]) -> str:
    for clave in (
        "Fecha de emisión",
        "Fecha de emision",
        "Fecha de Emisión",
        "Fecha emision",
    ):
        val = (fila.get(clave) or "").strip()
        if val:
            return val
    for clave, val in fila.items():
        nk = clave.lower().replace("ó", "o")
        if any(x in nk for x in ("modificado", "vcto", "pago")):
            continue
        if "fecha" in nk and "emisi" in nk and (val or "").strip():
            return str(val).strip()
    return ""


def _fecha_referencia_inactividad(periodo: str | None) -> tuple[int, int, int]:
    from calendar import monthrange
    from datetime import date

    hoy = date.today()
    if not periodo or len(periodo) != 6 or not periodo.isdigit():
        return hoy.year, hoy.month, hoy.day
    y, m = int(periodo[:4]), int(periodo[4:6])
    ultimo_dia = monthrange(y, m)[1]
    if (y, m) < (hoy.year, hoy.month):
        return y, m, ultimo_dia
    if (y, m) > (hoy.year, hoy.month):
        return y, m, ultimo_dia
    return hoy.year, hoy.month, hoy.day


def _periodo_es_actual(periodo: str | None) -> bool:
    from datetime import date

    if not periodo or len(periodo) != 6:
        return True
    hoy = date.today()
    return periodo == f"{hoy.year}{hoy.month:02d}"


def dias_habiles_desde(fecha: tuple[int, int, int], hasta: tuple[int, int, int] | None = None) -> int:
    from datetime import date, timedelta

    y, mo, d = fecha
    inicio = date(y, mo, d)
    if hasta:
        fin = date(*hasta)
    else:
        fin = date.today()
    if fin <= inicio:
        return 0
    cursor = inicio + timedelta(days=1)
    total = 0
    while cursor <= fin:
        if cursor.weekday() < 5:
            total += 1
        cursor += timedelta(days=1)
    return total


def calcular_inactividad(
    ventas: list[dict[str, str]],
    umbral_dias_habiles: int = 5,
    periodo: str | None = None,
) -> dict[str, Any]:
    ultima: tuple[int, int, int] | None = None
    ultima_txt = ""
    for fila in ventas:
        raw = _fecha_emision_fila(fila)
        parsed = parse_fecha_sunat(raw)
        if not parsed:
            continue
        if ultima is None or parsed > ultima:
            ultima = parsed
            ultima_txt = raw

    if not ultima:
        return {
            "ultima_emision": None,
            "dias_habiles": None,
            "umbral": umbral_dias_habiles,
            "alerta": False,
            "periodo_actual": _periodo_es_actual(periodo),
            "mensaje": "Sin comprobantes emitidos en el período consultado",
        }

    ref = _fecha_referencia_inactividad(periodo)
    dias = dias_habiles_desde(ultima, hasta=ref)
    es_actual = _periodo_es_actual(periodo)
    alerta = es_actual and dias > umbral_dias_habiles
    y, mo, d = ultima
    fecha_txt = ultima_txt or f"{d:02d}/{mo:02d}/{y}"
    if es_actual:
        mensaje = (
            f"Han transcurrido {dias} días hábiles desde el último comprobante emitido ({fecha_txt})"
        )
    else:
        mensaje = f"Última emisión en el período: {fecha_txt} ({dias} días hábiles hasta cierre del mes)"
    return {
        "ultima_emision": fecha_txt,
        "dias_habiles": dias,
        "umbral": umbral_dias_habiles,
        "alerta": alerta,
        "periodo_actual": es_actual,
        "mensaje": mensaje,
    }
