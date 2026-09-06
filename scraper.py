#!/usr/bin/env python3
"""
Sonda v4. Una sola pregunta: por que los pasajeros quedan en "1 adulto" con la
ventana de noviembre y en "2 adultos, 2 ninos" con la de diciembre.

El comportamiento es DETERMINISTA (dos barridos byte a byte iguales), asi que no
es una carrera de hidratacion como supuse. Esta sonda no arregla nada: instrumenta
el panel paso a paso en las dos fechas y enseña donde divergen.

Tarda ~2 minutos.
"""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
DIAG = RAIZ / "diagnostico"
RENFE = "https://www.renfe.com/es/es"
MADRID = ZoneInfo("Europe/Madrid")

CASOS = [
    ("noviembre", "Madrid (todas)", "Cuenca", "2026-11-06"),
    ("diciembre", "Madrid (todas)", "Cuenca", "2026-12-04"),
]

BOTONES = {
    "sumar adulto": "[aria-label='Añadir adulto']",
    "sumar niño >4": "[aria-label='Añadir niño mayor de 4']",
    "sumar niño <4": "[aria-label='Añadir niño menor de 4']",
    "quitar adulto": "[aria-label='Eliminar adulto']",
}


def ms_madrid(iso):
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, tzinfo=MADRID).timestamp() * 1000)


def valor(page):
    try:
        return (page.locator("#passengersSelection").first
                .get_attribute("value") or "").strip()
    except Exception as e:
        return f"<error: {str(e)[:60]}>"


def estado_botones(page, inf):
    for nombre, sel in BOTONES.items():
        try:
            loc = page.locator(sel)
            n = loc.count()
            vis = loc.first.is_visible() if n else False
            hab = loc.first.is_enabled() if n else False
            inf.append(f"      {nombre:14s} n={n} visible={vis} habilitado={hab}")
        except Exception as e:
            inf.append(f"      {nombre:14s} ERROR {str(e)[:60]}")


def caso(page, inf, etiqueta, origen, destino, fecha):
    inf.append(f"\n{'=' * 62}\nCASO {etiqueta.upper()}  {origen} -> {destino}  {fecha}\n{'=' * 62}")

    page.goto(RENFE, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.click("#onetrust-accept-btn-handler", timeout=5000)
    except PWTimeout:
        pass
    page.wait_for_timeout(1500)
    inf.append(f"  1. tras cargar la portada          value = '{valor(page)}'")

    # solo ida
    solo = False
    for sel in ["label:has-text('Viaje solo ida')", "label:has-text('Solo ida')"]:
        try:
            page.click(sel, timeout=2500)
            solo = True
            break
        except Exception:
            continue
    if not solo:
        try:
            solo = bool(page.evaluate(
                "() => { for (const e of document.querySelectorAll('label,button,span,div,a,input')) {"
                " const t = ((e.innerText||e.value||'')+'').trim().toLowerCase();"
                " if (t==='viaje solo ida'||t==='solo ida') { e.click(); return true; } } return false; }"))
        except Exception:
            pass
    page.wait_for_timeout(1000)
    inf.append(f"  2. tras poner SOLO IDA ({solo})      value = '{valor(page)}'")

    page.fill("#origin", origen)
    page.wait_for_timeout(1100)
    page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
    page.fill("#destination", destino)
    page.wait_for_timeout(1100)
    page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
    inf.append(f"  3. tras origen y destino           value = '{valor(page)}'")

    # fecha
    try:
        page.click("#first-input", timeout=6000)
    except Exception:
        try:
            page.click("input[placeholder*='Fecha']", timeout=6000)
        except Exception:
            inf.append("     [!] no se pudo abrir el calendario")
    page.wait_for_timeout(900)
    ms = ms_madrid(fecha)
    puesta = False
    for _ in range(24):
        loc = page.locator(f".lightpick__day[data-time='{ms}']"
                           ":not(.is-disabled):not(.is-previous-month)").first
        if loc.count() and loc.is_visible():
            loc.click(timeout=4000)
            puesta = True
            break
        try:
            page.click(".lightpick__next-action", timeout=3000)
            page.wait_for_timeout(300)
        except Exception:
            break
    for sel in ["button:has-text('Aceptar')", "button:has-text('Listo')"]:
        try:
            page.click(sel, timeout=2000)
        except Exception:
            pass
    page.wait_for_timeout(700)
    inf.append(f"  4. tras la fecha ({puesta})          value = '{valor(page)}'")

    # panel de pasajeros
    abierto = None
    for sel in ["#passengersSelection", "[class*='passenger'] button"]:
        try:
            page.click(sel, timeout=5000)
            abierto = sel
            break
        except Exception:
            continue
    page.wait_for_timeout(1000)
    inf.append(f"  5. panel abierto con {abierto}")
    inf.append(f"                                     value = '{valor(page)}'")
    inf.append("     estado de los botones:")
    estado_botones(page, inf)

    # clic a clic, leyendo despues de cada uno
    for i in range(1):
        try:
            page.click(BOTONES["sumar adulto"], timeout=3000)
            page.wait_for_timeout(600)
            inf.append(f"  6.{i+1} tras 'Añadir adulto'          value = '{valor(page)}'")
        except Exception as e:
            inf.append(f"  6.{i+1} FALLO al pulsar adulto: {str(e)[:100]}")

    for i in range(2):
        try:
            page.click(BOTONES["sumar niño >4"], timeout=3000)
            page.wait_for_timeout(600)
            inf.append(f"  7.{i+1} tras 'Añadir niño mayor de 4' value = '{valor(page)}'")
        except Exception as e:
            inf.append(f"  7.{i+1} FALLO al pulsar niño: {str(e)[:100]}")

    page.screenshot(path=str(DIAG / f"pax-{etiqueta}.png"))

    try:
        page.click("button:has-text('Listo')", timeout=4000)
        page.wait_for_timeout(800)
        inf.append(f"  8. tras pulsar 'Listo'             value = '{valor(page)}'")
    except Exception as e:
        inf.append(f"  8. FALLO al pulsar Listo: {str(e)[:100]}")

    # ¿sobrevive al click de buscar?
    try:
        b = page.locator("button:has-text('Buscar billete')").first
        b.scroll_into_view_if_needed(timeout=3000)
        inf.append(f"  9. justo antes de buscar           value = '{valor(page)}'")
    except Exception:
        pass


def main():
    DIAG.mkdir(exist_ok=True)
    inf = ["SONDA v4 - por que los pasajeros no se fijan en noviembre",
           f"generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
           "Renfe arranca en '1 adulto'. Objetivo: 2 adultos, 2 niños."]

    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(locale="es-ES", timezone_id="Europe/Madrid",
                              reduced_motion="reduce",
                              viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        page.set_default_timeout(20_000)
        for etiqueta, o, d, f in CASOS:
            try:
                caso(page, inf, etiqueta, o, d, f)
            except Exception as e:
                inf.append(f"  [!] el caso {etiqueta} reventó: {str(e)[:200]}")
        nav.close()

    texto = "\n".join(inf)
    (DIAG / "sonda.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
