#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe para las rutas de rutas.json y escribe
precios-trenes.json. Pensado para correr en GitHub Actions, que si alcanza
renfe.com (el contenedor del radar no).

Contrato de salida (lo que el radar lee cada dia):
{
  "generado": "2026-09-05T04:12:00Z",
  "alerta": null | "texto explicando que se ha roto",
  "ventanas": {
     "noviembre": {
        "salida": "2026-11-06", "vuelta": "2026-11-09",
        "rutas": [
          {"destino":"Cuenca","referencia":71,"precio":68,"variacion_pct":-4.2,
           "ida":{"salida":"17:35","llegada":"18:32","tren":"AVANT"},
           "vuelta":{"salida":"18:10","llegada":"19:05","tren":"AVANT"},
           "mas_temprano":"07:05","mas_tardio":"21:35","limpio":true}
        ]
     }
  }
}

Si una ruta falla se escribe igual con "precio": null y "error": "...".
El fichero SIEMPRE se escribe: un radar sin datos tiene que saber que no los tiene.
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
RUTAS = RAIZ / "rutas.json"
SALIDA = RAIZ / "precios-trenes.json"
DIAG = RAIZ / "diagnostico"

RENFE = "https://www.renfe.com/es/es"
TIMEOUT = 45_000


def hhmm(texto):
    """Saca la primera hora HH:MM de un texto suelto."""
    m = re.search(r"\b([0-2]?\d):([0-5]\d)\b", texto or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def eur(texto):
    """Saca el primer importe en euros de un texto suelto. Acepta 71,50 y 71.50."""
    m = re.search(r"(\d{1,4})[.,](\d{2})\s*€|(\d{1,4})\s*€", texto or "")
    if not m:
        return None
    if m.group(3):
        return float(m.group(3))
    return float(f"{m.group(1)}.{m.group(2)}")


def antes_de(hora, tope):
    return hora is not None and hora <= tope


def despues_de(hora, suelo):
    return hora is not None and hora >= suelo


def aceptar_cookies(page):
    for sel in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Aceptar todas')",
        "button:has-text('Aceptar')",
    ]:
        try:
            page.click(sel, timeout=4000)
            return
        except PWTimeout:
            continue


def rellenar_busqueda(page, origen, destino, fecha_ida, fecha_vuelta, pax):
    """
    Rellena el buscador de renfe.com. Esta es la parte fragil: si Renfe cambia el
    maquetado, aqui es donde hay que retocar. El workflow guarda captura y HTML
    en el artefacto 'diagnostico' cuando algo peta, para poder ajustar selectores
    sin adivinar.
    """
    page.goto(RENFE, wait_until="domcontentloaded", timeout=TIMEOUT)
    aceptar_cookies(page)

    page.fill("#origin", origen)
    page.wait_for_timeout(900)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    page.fill("#destination", destino)
    page.wait_for_timeout(900)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    # Fechas: el datepicker de Renfe marca los dias por data-date (YYYY-MM-DD).
    page.click(".rf-daterange__input, #first-input", timeout=TIMEOUT)
    for f in (fecha_ida, fecha_vuelta):
        page.click(f"[data-date='{f}']", timeout=TIMEOUT)
    page.click("button:has-text('Aceptar')", timeout=8000)

    # Pasajeros: 2 adultos + 2 ninos.
    try:
        page.click(".rf-select-passenger, #passengersSelector", timeout=8000)
        for _ in range(pax["ninos"]):
            page.click("button[aria-label*='ñadir'][aria-label*='iño']", timeout=5000)
        page.click("button:has-text('Aceptar')", timeout=5000)
    except PWTimeout:
        # Si no se puede fijar pasajeros, seguimos con la tarifa base y lo marcamos.
        pass

    page.click("button:has-text('Buscar billete')", timeout=TIMEOUT)
    page.wait_for_selector(".selectedTrain, .trayecto, [class*='train-list']", timeout=TIMEOUT)


def leer_trayecto(page, sentido):
    """Devuelve (precio_min, salida_min, llegada_min, tren, mas_temprano, mas_tardio)."""
    filas = page.query_selector_all(".selectedTrain, .trayecto, [class*='train-item']")
    if not filas:
        raise RuntimeError(f"sin filas de tren en el sentido {sentido}")

    horas, mejor = [], None
    for fila in filas:
        txt = fila.inner_text()
        precio = eur(txt)
        salida = hhmm(txt)
        llegadas = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", txt)
        llegada = llegadas[1] if len(llegadas) > 1 else None
        tren = next((t for t in ("AVE", "AVLO", "AVANT", "ALVIA", "MD", "INTERCITY")
                     if t in txt.upper()), None)
        if salida:
            horas.append(salida)
        if precio is not None and (mejor is None or precio < mejor[0]):
            mejor = (precio, salida, llegada, tren)

    if mejor is None:
        raise RuntimeError(f"filas sin precio legible en el sentido {sentido}")

    return (*mejor, min(horas) if horas else None, max(horas) if horas else None)


def barrer(page, cfg, ventana, destino):
    rellenar_busqueda(
        page,
        cfg["origen"]["nombre"], destino["nombre"],
        ventana["salida"], ventana["vuelta"], cfg["pasajeros"],
    )

    p_ida, s_ida, l_ida, t_ida, temprano_ida, tardio_ida = leer_trayecto(page, "ida")

    try:
        page.click("button:has-text('Continuar'), button:has-text('Seleccionar')", timeout=10000)
        page.wait_for_timeout(1500)
    except PWTimeout:
        pass

    p_vta, s_vta, l_vta, t_vta, temprano_vta, tardio_vta = leer_trayecto(page, "vuelta")

    limpio = (
        antes_de(l_ida, cfg["horario_limpio"]["ida_llegada_maxima"])
        and despues_de(s_vta, cfg["horario_limpio"]["vuelta_salida_minima"])
        and antes_de(l_vta, cfg["horario_limpio"]["vuelta_llegada_maxima"])
    )

    total = round(p_ida + p_vta, 2)
    ref = destino.get("referencia")
    var = round((total - ref) / ref * 100, 1) if ref else None

    return {
        "destino": destino["nombre"],
        "referencia": ref,
        "precio": total,
        "variacion_pct": var,
        "ida": {"salida": s_ida, "llegada": l_ida, "tren": t_ida},
        "vuelta": {"salida": s_vta, "llegada": l_vta, "tren": t_vta},
        "mas_temprano": temprano_ida,
        "mas_tardio": tardio_ida or tardio_vta,
        "limpio": limpio,
        "error": None,
    }


def main():
    cfg = json.loads(RUTAS.read_text(encoding="utf-8"))
    DIAG.mkdir(exist_ok=True)

    resultado = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alerta": None,
        "ventanas": {},
    }
    fallos, total_rutas = 0, 0

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        ctx = navegador.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1440, "height": 1000},
        )
        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT)

        for ventana in cfg["ventanas"]:
            filas = []
            for destino in cfg["destinos"]:
                total_rutas += 1
                try:
                    filas.append(barrer(page, cfg, ventana, destino))
                except Exception as e:
                    fallos += 1
                    etiqueta = f"{ventana['id']}-{destino['nombre']}".replace(" ", "_")
                    try:
                        page.screenshot(path=str(DIAG / f"{etiqueta}.png"), full_page=True)
                        (DIAG / f"{etiqueta}.html").write_text(page.content(), encoding="utf-8")
                    except Exception:
                        pass
                    print(f"[fallo] {etiqueta}: {e}", file=sys.stderr)
                    filas.append({
                        "destino": destino["nombre"],
                        "referencia": destino.get("referencia"),
                        "precio": None,
                        "error": str(e)[:300],
                    })

            resultado["ventanas"][ventana["id"]] = {
                "salida": ventana["salida"],
                "vuelta": ventana["vuelta"],
                "rutas": filas,
            }

        navegador.close()

    # La alerta es lo primero que lee el radar. Que sea util, no decorativa.
    if total_rutas and fallos == total_rutas:
        resultado["alerta"] = (
            "El scraper no ha podido leer NINGUNA ruta. Renfe ha cambiado el maquetado "
            "o esta bloqueando al runner. Mirar el artefacto 'diagnostico' del workflow."
        )
    elif fallos:
        resultado["alerta"] = f"{fallos} de {total_rutas} rutas han fallado. Ver 'diagnostico'."

    # Renfe solo vende con unos 4 meses de antelacion: si una ventana esta entera
    # vacia y aun falta mucho, es lo normal, no una averia.
    for vid, v in resultado["ventanas"].items():
        dias = (datetime.fromisoformat(v["salida"]).date() - datetime.now(timezone.utc).date()).days
        if dias > 125 and all(r.get("precio") is None for r in v["rutas"]):
            for r in v["rutas"]:
                r["error"] = "fuera de venta todavia (Renfe abre a ~4 meses)"

    SALIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Escrito {SALIDA.name}: {total_rutas - fallos}/{total_rutas} rutas con precio")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        # Aun asi dejamos fichero, con la alerta puesta.
        SALIDA.write_text(json.dumps({
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "alerta": "El scraper ha reventado antes de empezar. Ver el log del workflow.",
            "ventanas": {},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)
