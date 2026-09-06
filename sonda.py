#!/usr/bin/env python3
"""
Sonda de diagnostico, v3. Foco: LA PAGINA DE RESULTADOS.

El calendario y los pasajeros ya estan resueltos y confirmados. Lo unico a oscuras
es que aparece despues de pulsar "Buscar billete": si navega a otro dominio, si
abre pestaña nueva, si mete un interstitial, y como son las filas de tren.

Hace el camino completo una sola vez (Madrid-Barcelona) y vuelca:
  - a donde acaba (url y titulo), y si se abrio otra pestaña
  - los elementos que contienen a la vez un precio y una hora, con su clase real
  - el HTML de la pagina de resultados
  - capturas antes y despues de buscar
Tarda ~1 minuto.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
DIAG = RAIZ / "diagnostico"
RENFE = "https://www.renfe.com/es/es"
MADRID = ZoneInfo("Europe/Madrid")

IDA, VUELTA = "2026-11-06", "2026-11-09"

# Mismo detector por forma que usa el scraper, pero devolviendo tambien la clase
# real del elemento, que es lo que aqui interesa documentar.
JS_CANDIDATOS = r"""
() => {
  const rePrecio = /(\d{1,4}[.,]\d{2}\s*€)|(\d{1,4}\s*€)/;
  const reHora   = /\b[0-2]?\d:[0-5]\d\b/;
  const out = [];
  for (const el of document.querySelectorAll('div,li,article,tr,section,a')) {
    const t = el.innerText || '';
    if (t.length < 10 || t.length > 900) continue;
    if (!rePrecio.test(t) || !reHora.test(t)) continue;
    let hijoCumple = false;
    for (const h of el.children) {
      const ht = h.innerText || '';
      if (rePrecio.test(ht) && reHora.test(ht)) { hijoCumple = true; break; }
    }
    if (hijoCumple) continue;
    out.push({
      tag: el.tagName,
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 120),
      id: el.id || '',
      txt: t.replace(/\s+/g, ' ').slice(0, 200)
    });
  }
  return out.slice(0, 30);
}
"""


def ms_madrid(iso):
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, tzinfo=MADRID).timestamp() * 1000)


def elegir_fecha(page, iso, inf):
    ms = ms_madrid(iso)
    for i in range(24):
        loc = page.locator(f".lightpick__day[data-time='{ms}']"
                           ":not(.is-disabled):not(.is-previous-month)").first
        if loc.count() and loc.is_visible():
            loc.click(timeout=4000)
            inf.append(f"  fecha {iso} pinchada (avanzando {i} meses)")
            return True
        try:
            page.click(".lightpick__next-action", timeout=4000)
            page.wait_for_timeout(350)
        except Exception:
            break
    inf.append(f"  [ ] fecha {iso} NO pinchada")
    return False


def main():
    DIAG.mkdir(exist_ok=True)
    inf = ["SONDA RENFE v3 - foco en PAGINA DE RESULTADOS",
           f"generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
           f"ruta de prueba: Madrid -> Barcelona, {IDA} / {VUELTA}"]

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

        inf.append("\n### FECHAS")
        page.click("#first-input", timeout=8000)
        page.wait_for_timeout(1000)
        elegir_fecha(page, IDA, inf)
        page.wait_for_timeout(400)
        elegir_fecha(page, VUELTA, inf)
        page.wait_for_timeout(400)
        for sel in ["button:has-text('Aceptar')", "button:has-text('Listo')"]:
            try:
                page.click(sel, timeout=3000); break
            except Exception:
                continue

        inf.append("\n### PASAJEROS")
        try:
            page.click("#passengersSelection", timeout=6000)
            page.wait_for_timeout(800)
            page.click("[aria-label='Añadir adulto']", timeout=3000)
            page.wait_for_timeout(350)
            page.click("[aria-label='Añadir niño mayor de 4']", timeout=3000)
            page.wait_for_timeout(350)
            page.click("[aria-label='Añadir niño mayor de 4']", timeout=3000)
            page.wait_for_timeout(350)
            page.click("button:has-text('Listo')", timeout=4000)
            page.wait_for_timeout(600)
            v = page.locator("#passengersSelection").first.get_attribute("value")
            inf.append(f"  value del desplegable tras tocarlo: '{v}'")
        except Exception as e:
            inf.append(f"  [!] fallo poniendo pasajeros: {str(e)[:150]}")

        page.screenshot(path=str(DIAG / "3-antes-de-buscar.png"))
        antes_url = page.url
        antes_pestanas = len(ctx.pages)

        inf.append("\n### PULSAR BUSCAR")
        modo = "no pinchado"
        try:
            b = page.locator("button:has-text('Buscar billete')").first
            b.scroll_into_view_if_needed(timeout=3000)
            try:
                b.click(timeout=8000); modo = "click normal"
            except Exception:
                b.evaluate("e => e.click()"); modo = "click por DOM"
        except Exception as e:
            inf.append(f"  [!] {str(e)[:150]}")
        inf.append(f"  modo de click: {modo}")

        # Esperar a que pase algo: navegacion, pestaña nueva o filas.
        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state("networkidle", timeout=25_000)
        except PWTimeout:
            inf.append("  (networkidle no llego en 25s, seguimos igual)")

        activa = [p for p in ctx.pages if not p.is_closed()][-1]
        inf.append(f"\n  url antes: {antes_url}")
        inf.append(f"  url ahora: {activa.url}")
        inf.append(f"  titulo:    {activa.title()}")
        inf.append(f"  pestañas:  {antes_pestanas} -> {len(ctx.pages)}"
                   f"{'  (SE ABRIO UNA NUEVA)' if len(ctx.pages) > antes_pestanas else ''}")

        activa.screenshot(path=str(DIAG / "4-resultados.png"))

        # Reintento por si tarda en pintar
        cands = []
        for intento in range(6):
            cands = activa.evaluate(JS_CANDIDATOS) or []
            if cands:
                inf.append(f"  filas detectadas en el intento {intento + 1}")
                break
            activa.wait_for_timeout(4000)

        inf.append(f"\n### CANDIDATOS A FILA DE TREN ({len(cands)})")
        if not cands:
            inf.append("  NINGUNO. No hay ningun elemento con precio y hora a la vez.")
            texto = (activa.inner_text("body") or "")[:2500]
            inf.append(f"\n  Texto visible de la pagina (2500 primeros chars):\n{texto}")
        else:
            for c in cands[:20]:
                inf.append(f"\n  <{c['tag']}> id='{c['id']}'\n    class='{c['cls']}'"
                           f"\n    txt: {c['txt']}")

        try:
            (DIAG / "resultados.html").write_text(activa.content()[:400_000],
                                                  encoding="utf-8")
            inf.append("\nresultados.html guardado")
        except Exception as e:
            inf.append(f"\nno se pudo guardar el HTML: {str(e)[:120]}")

        nav.close()

    texto = "\n".join(inf)
    (DIAG / "sonda.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
