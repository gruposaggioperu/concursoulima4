#!/usr/bin/env python3
"""Muestra columnas reales de compras RCE y prueba Top 5 proveedores."""

import json
import sys
from pathlib import Path

from sunat_sire import (
    SunatError,
    descargar_propuesta,
    descargar_top_proveedores,
    obtener_token,
)

ROOT = Path(__file__).resolve().parent
PERIODO = sys.argv[1] if len(sys.argv) > 1 else "202508"


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    pm = next(c for c in cfg["clientes_prueba"] if c["ruc"] == "20510673124")

    print(f"=== PRIME MUSIC · período {PERIODO} ===\n")
    token = obtener_token(
        pm["ruc"], pm["usuario"], pm["clave"], pm["client_id"], pm["client_secret"]
    )

    print("--- 1) COLUMNAS COMPRAS (RCE propuesta) ---")
    try:
        filas = descargar_propuesta(token, PERIODO, "compras")
        print(f"Registros: {len(filas)}")
        if not filas:
            print("(sin filas en este período)\n")
        else:
            cols = list(filas[0].keys())
            print(f"Total columnas: {len(cols)}\n")
            for i, c in enumerate(cols, 1):
                print(f"  {i:2}. {c}")

            print("\n--- 2) MUESTRA (1ra fila · campos proveedor/montos) ---")
            f = filas[0]
            claves_interes = [
                k
                for k in cols
                if any(
                    x in k.lower()
                    for x in (
                        "ruc",
                        "doc",
                        "identidad",
                        "emisor",
                        "proveedor",
                        "apellido",
                        "razon",
                        "social",
                        "denomin",
                        "total",
                        "importe",
                        "monto",
                    )
                )
            ]
            for k in claves_interes:
                print(f"  [{k}] = {f.get(k, '')}")

            print("\n--- 3) CAMPOS NORMALIZADOS (backend) ---")
            for k in ("RUC Proveedor", "Nombre Proveedor", "Total CP", "Tipo CP/Doc."):
                if k in f:
                    print(f"  {k} = {f[k]}")

            print("\n--- 4) TOP 5 manual (RUC Proveedor + Nombre Proveedor + Total CP) ---")
            agg: dict[str, dict] = {}
            for row in filas:
                ruc = row.get("RUC Proveedor", "")
                nom = row.get("Nombre Proveedor") or row.get(
                    "Apellidos Nombres/ Razón Social", "?"
                )
                key = ruc or nom
                try:
                    m = float(str(row.get("Total CP", "0")).replace(",", ""))
                except ValueError:
                    m = 0
                if key not in agg:
                    agg[key] = {"nombre": nom, "ruc": ruc, "monto": 0.0}
                agg[key]["monto"] += m
            top = sorted(agg.values(), key=lambda x: x["monto"], reverse=True)[:5]
            for i, p in enumerate(top, 1):
                ruc_txt = f" ({p['ruc']})" if p["ruc"] else ""
                print(f"  {i}. {p['nombre']}{ruc_txt} · S/ {p['monto']:,.2f}")
    except SunatError as e:
        print(f"Error compras: {e}\n")

    print("\n--- 5) TOP 5 OFICIAL SUNAT (reporte estadístico 5.54) ---")
    try:
        top = descargar_top_proveedores(token, pm["ruc"], PERIODO)
        for i, p in enumerate(top, 1):
            pct = f" ({p['porcentaje']})" if p.get("porcentaje") else ""
            print(f"  {i}. {p['nombre']} · S/ {p['monto']:,.2f}{pct}")
    except SunatError as e:
        print(f"  No disponible: {e}")


if __name__ == "__main__":
    main()
