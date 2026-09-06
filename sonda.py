#!/usr/bin/env python3
"""
Sonda de diagnostico. ~1 minuto, no barre precios.

La primera sonda ya resolvio el calendario. Esta version se centra en lo unico
que queda a oscuras: el selector de PASAJEROS. Renfe arranca en "1 adulto" y si
no conseguimos ponerlo en 2 adultos + 2 ninos, los precios no valen para el radar.

Comprueba ademas que la seleccion de fecha funciona ya de verdad, calculando el
data-time en hora de Madrid, que era el fallo original.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
DIAG = RAIZ / "diagnostico"
RENFE = "https://www.renfe.com/es/es"
MADRID = ZoneInfo("Europe/Madrid")

FECHA_PRUEBA = "2026-11-06"     # dentro de los 4 meses de venta de Renfe


def ms_madrid(iso):
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, tzinfo=MADRID).timestamp() * 1000)


def inspeccionar(page, etiqueta, selectores, inf):
    inf.append(f"\n### {etiqueta}")
    for sel in selectores:
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n == 0:
                inf.append(f"  [ ] {sel}   (0)")
                continue
            p = loc.first
            vis = p.is_visible()
            try:
                muestra = (p.inner_text() or "")[:70].replace("\n", " ")
            except Exception:
                muestra = ""
            inf.append(f"  [{'X' if vis else '~'}] {sel}   ({n})  {muestra}")
        except Exception as e:
            inf.append(f"  [!] {sel}   ERROR {str(e)[:80]}")


def main():
    DIAG.mkdir(exist_ok=True)
    inf = ["SONDA RENFE v2 - foco en PASAJEROS",
           f"generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
           "Leyenda: [X] existe y visible · [~] existe oculto · [ ] no existe"]

    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(locale="es-ES", timezone_id="Europe/Madrid",
                              viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        page.set_default_timeout(20_000)

        page.goto(RENFE, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.click("#onetrust-accept-btn-handler", timeout=5000)
        except PWTimeout:
            pass
        page.wait_for_timeout(1200)

        page.fill("#origin", "Madrid")
        page.wait_for_timeout(1200)
        page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
        page.fill("#destination", "Barcelona")
        page.wait_for_timeout(1200)
        page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
        inf.append("\norigen y destino rellenos con #origin / #destination")

        # --- comprobar que la fecha YA se puede pinchar con el calculo correcto ---
        page.click("#first-input", timeout=8000)
        page.wait_for_timeout(1200)
        ms = ms_madrid(FECHA_PRUEBA)
        inf.append(f"\n### PRUEBA DE FECHA {FECHA_PRUEBA}")
        inf.append(f"  data-time calculado en hora de Madrid: {ms}")

        pinchada = False
        for intento in range(24):
            loc = page.locator(f".lightpick__day[data-time='{ms}']"
                               ":not(.is-disabled):not(.is-previous-month)").first
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                inf.append(f"  [X] PINCHADA tras avanzar {intento} meses")
                pinchada = True
                break
            crudo = page.locator(f".lightpick__day[data-time='{ms}']").first
            if crudo.count():
                clases = crudo.get_attribute("class") or ""
                inf.append(f"  celda encontrada pero con clases: {clases}")
                if "is-disabled" in clases:
                    inf.append("  -> deshabilitada: fuera de venta todavia")
                    break
            try:
                page.click(".lightpick__next-action", timeout=4000)
                page.wait_for_timeout(350)
            except Exception:
                break
        if not pinchada:
            inf.append("  [ ] NO se pudo pinchar. Revisar.")

        page.screenshot(path=str(DIAG / "1-calendario.png"))

        # --- lo importante de esta sonda: el panel de pasajeros ---
        inf.append("\n### ABRIR PANEL DE PASAJEROS")
        abierto = None
        for sel in ["[class*='passenger'] button", "button[class*='passenger']",
                    "#passengersSelector", "[class*='passenger']",
                    "text=1 adulto", "[class*='pasajero']"]:
            try:
                page.click(sel, timeout=4000)
                abierto = sel
                inf.append(f"  abierto con: {sel}")
                break
            except Exception:
                continue
        if not abierto:
            inf.append("  [ ] no se pudo abrir el panel con ningun selector")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(DIAG / "2-pasajeros.png"))

        inspeccionar(page, "BOTONES DE SUMAR", [
            "button[aria-label*='ñadir']", "button[aria-label*='umar']",
            "button[aria-label*='ncrementar']", "[class*='plus']",
            "button:has-text('+')", "[class*='add']", "[class*='increment']",
        ], inf)

        inspeccionar(page, "CONTADORES / TIPOS DE PASAJERO", [
            "[class*='adult']", "[class*='child']", "[class*='nino']",
            "[class*='counter']", "input[type='number']", "select",
        ], inf)

        # El volcado que de verdad resuelve: el HTML del panel abierto.
        inf.append("\n### HTML DEL PANEL DE PASAJEROS")
        guardado = False
        for sel in ["[class*='passenger']", "[class*='pasajero']", "[role='dialog']"]:
            try:
                loc = page.locator(sel)
                if not loc.count():
                    continue
                # nos quedamos con el contenedor mas grande, que sera el panel
                mejor, mejor_len = None, 0
                for i in range(min(loc.count(), 40)):
                    h = loc.nth(i).evaluate("e => e.outerHTML")
                    if len(h) > mejor_len:
                        mejor, mejor_len = h, len(h)
                if mejor and mejor_len > 200:
                    (DIAG / "pasajeros.html").write_text(mejor[:200_000], encoding="utf-8")
                    inf.append(f"  guardado desde {sel} ({mejor_len} chars)")
                    inf.append(f"  primeros 1500 chars:\n{mejor[:1500]}")
                    guardado = True
                    break
            except Exception as e:
                inf.append(f"  error con {sel}: {str(e)[:100]}")
        if not guardado:
            inf.append("  no se pudo volcar el panel")

        nav.close()

    texto = "\n".join(inf)
    (DIAG / "sonda.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
