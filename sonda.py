#!/usr/bin/env python3
"""
Sonda de diagnostico. Tarda ~1 minuto y NO barre precios: solo abre renfe.com,
despliega el calendario y cuenta que selectores existen de verdad.

Escribe diagnostico/sonda.txt (unos pocos KB) y dos capturas pequenas.
Con eso se ajustan los selectores del scraper sin adivinar.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
DIAG = RAIZ / "diagnostico"
RENFE = "https://www.renfe.com/es/es"

# Todo lo que se me ocurre que Renfe pueda estar usando. La sonda dice cual vive.
CANDIDATOS = {
    "origen": ["#origin", "#origin-input", "input[name='origin']",
               "input[placeholder*='Origen']", "[data-testid*='origin']"],
    "destino": ["#destination", "#destination-input", "input[name='destination']",
                "input[placeholder*='Destino']", "[data-testid*='destination']"],
    "abrir_calendario": [".rf-daterange__input", "#first-input", ".lightpick__input",
                         "input[placeholder*='Fecha']", "[class*='daterange']"],
    "contenedor_calendario": [".lightpick", ".rf-daterange", "[class*='calendar']",
                              "[class*='datepicker']", "[role='dialog']"],
    "celda_dia": ["[data-date]", ".lightpick__day", "[data-time]",
                  "td[data-day]", "[class*='day']:not([class*='header'])",
                  "button[aria-label*='noviembre']", "[aria-label*='de noviembre']"],
    "cabecera_mes": [".lightpick__month-title", "[class*='month-title']",
                     "[class*='monthName']", "[class*='month']"],
    "mes_siguiente": [".lightpick__next-action", "[class*='next']",
                      "button[aria-label*='iguiente']", "[aria-label*='Next']"],
    "pasajeros": [".rf-select-passenger", "#passengersSelector",
                  "[class*='passenger']", "button[aria-label*='asajero']"],
    "buscar": ["button:has-text('Buscar billete')", "button:has-text('Buscar')",
               "[class*='btn-search']", "button[type='submit']"],
}


def inspeccionar(page, etiqueta, selectores, informe):
    informe.append(f"\n### {etiqueta}")
    for sel in selectores:
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n == 0:
                informe.append(f"  [ ] {sel}   (0)")
                continue
            primero = loc.first
            visible = primero.is_visible()
            muestra = ""
            try:
                muestra = (primero.inner_text() or "")[:60].replace("\n", " ")
            except Exception:
                pass
            marca = "X" if visible else "~"
            informe.append(f"  [{marca}] {sel}   ({n})  {muestra}")
        except Exception as e:
            informe.append(f"  [!] {sel}   ERROR {str(e)[:80]}")


def main():
    DIAG.mkdir(exist_ok=True)
    informe = [
        "SONDA RENFE",
        f"generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Leyenda: [X] existe y visible · [~] existe oculto · [ ] no existe",
    ]

    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(locale="es-ES", timezone_id="Europe/Madrid",
                              viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        page.set_default_timeout(20_000)

        page.goto(RENFE, wait_until="domcontentloaded", timeout=60_000)
        informe.append(f"\nurl tras cargar: {page.url}")
        informe.append(f"titulo: {page.title()}")

        for sel in ["#onetrust-accept-btn-handler",
                    "button:has-text('Aceptar todas')",
                    "button:has-text('Aceptar')"]:
            try:
                page.click(sel, timeout=4000)
                informe.append(f"cookies aceptadas con: {sel}")
                break
            except PWTimeout:
                continue
        page.wait_for_timeout(1500)

        inspeccionar(page, "ORIGEN", CANDIDATOS["origen"], informe)
        inspeccionar(page, "DESTINO", CANDIDATOS["destino"], informe)
        inspeccionar(page, "ABRIR CALENDARIO", CANDIDATOS["abrir_calendario"], informe)
        inspeccionar(page, "PASAJEROS", CANDIDATOS["pasajeros"], informe)
        inspeccionar(page, "BOTON BUSCAR", CANDIDATOS["buscar"], informe)

        page.screenshot(path=str(DIAG / "1-portada.png"))

        # Rellenamos origen y destino para que el calendario se comporte como en real.
        for sel in CANDIDATOS["origen"]:
            try:
                page.fill(sel, "Madrid", timeout=5000)
                page.wait_for_timeout(1200)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                informe.append(f"\norigen relleno con: {sel}")
                break
            except Exception:
                continue
        for sel in CANDIDATOS["destino"]:
            try:
                page.fill(sel, "Barcelona", timeout=5000)
                page.wait_for_timeout(1200)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                informe.append(f"destino relleno con: {sel}")
                break
            except Exception:
                continue

        page.wait_for_timeout(1200)

        abierto = None
        for sel in CANDIDATOS["abrir_calendario"]:
            try:
                page.click(sel, timeout=5000)
                abierto = sel
                break
            except Exception:
                continue
        informe.append(f"\ncalendario abierto con: {abierto}")
        page.wait_for_timeout(2000)

        inspeccionar(page, "CONTENEDOR CALENDARIO", CANDIDATOS["contenedor_calendario"], informe)
        inspeccionar(page, "CELDA DE DIA", CANDIDATOS["celda_dia"], informe)
        inspeccionar(page, "CABECERA DE MES", CANDIDATOS["cabecera_mes"], informe)
        inspeccionar(page, "MES SIGUIENTE", CANDIDATOS["mes_siguiente"], informe)

        page.screenshot(path=str(DIAG / "2-calendario.png"))

        # Lo mas util de todo: el HTML real de una celda de dia cualquiera.
        informe.append("\n### MUESTRA DE CELDAS DE DIA (outerHTML de las 5 primeras)")
        encontrado = False
        for sel in ["[data-date]", ".lightpick__day", "[data-time]",
                    "td[data-day]", "[class*='day']"]:
            try:
                loc = page.locator(sel)
                if loc.count() == 0:
                    continue
                encontrado = True
                informe.append(f"\n-- con selector {sel} ({loc.count()} celdas) --")
                for i in range(min(5, loc.count())):
                    html = loc.nth(i).evaluate("e => e.outerHTML")
                    informe.append(f"  {html[:300]}")
                break
            except Exception as e:
                informe.append(f"  error con {sel}: {str(e)[:100]}")
        if not encontrado:
            informe.append("  NINGUN selector de dia ha dado resultados.")

        # Y el contenedor entero, recortado, por si hace falta mirarlo a mano.
        for sel in CANDIDATOS["contenedor_calendario"]:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    html = loc.evaluate("e => e.outerHTML")
                    (DIAG / "calendario.html").write_text(html[:200_000], encoding="utf-8")
                    informe.append(f"\ncalendario.html guardado desde: {sel} "
                                   f"({len(html)} chars, recortado a 200k)")
                    break
            except Exception:
                continue

        nav.close()

    texto = "\n".join(informe)
    (DIAG / "sonda.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
