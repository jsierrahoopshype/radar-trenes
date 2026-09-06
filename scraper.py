#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe y escribe precios-trenes.json.

Version 5. Cambio de metodo en la deteccion de filas.

Historial, para no repetir errores:
  v1  Insistia 63 veces con el mismo fallo y agotaba los 45 min del job.
      -> Se rinde a los 3 fallos seguidos.
  v2  data-time calculado en UTC. Lightpick lo guarda en hora LOCAL DE MADRID.
      -> zoneinfo("Europe/Madrid"). Resuelto y confirmado.
  v3  Cerraba el panel de pasajeros con "Aceptar"; el componente lo llama "Listo".
      El panel tapaba el boton de buscar y el click moria por elemento cubierto.
      -> Se cierra con "Listo" + click robusto. Resuelto y confirmado.
  v4  Buscaba las filas de tren por clases inventadas (.selectedTrain, .trayecto...).
      Ninguna existe. Mismo error de fondo tres veces: adivinar nombres de clase.
      -> AHORA LAS FILAS SE DETECTAN POR FORMA, NO POR CLASE.

Como funciona la deteccion por forma: una fila de tren es el elemento MAS PEQUENO
que contiene a la vez un importe en euros y una hora. Se busca en el DOM con JS y
se descartan los contenedores cuyos hijos ya cumplen. No depende de como Renfe
llame a sus clases, asi que sobrevive a rediseños.
"""

import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
RUTAS = RAIZ / "rutas.json"
SALIDA = RAIZ / "precios-trenes.json"
DIAG = RAIZ / "diagnostico"

RENFE = "https://www.renfe.com/es/es"
MADRID = ZoneInfo("Europe/Madrid")
T = 20_000
FALLOS_SEGUIDOS_MAX = 3
MAX_DIAGNOSTICOS = 3

# El corazon de la version 5: filas por forma, no por clase.
JS_FILAS = r"""
() => {
  const rePrecio = /(\d{1,4}[.,]\d{2}\s*€)|(\d{1,4}\s*€)/;
  const reHora   = /\b[0-2]?\d:[0-5]\d\b/;
  const filas = [];
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
    filas.push(t.replace(/\s+/g, ' ').trim());
  }
  return filas;
}
"""

JS_HAY_FILAS = "() => { const f = (" + JS_FILAS.strip() + ")(); return f.length > 0; }"


class FueraDeVenta(Exception):
    pass


def ms_madrid(iso):
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, tzinfo=MADRID).timestamp() * 1000)


def hhmm(t):
    m = re.search(r"\b([0-2]?\d):([0-5]\d)\b", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def eur(t):
    m = re.search(r"(\d{1,4})[.,](\d{2})\s*€|(\d{1,4})\s*€", t or "")
    if not m:
        return None
    return float(m.group(3)) if m.group(3) else float(f"{m.group(1)}.{m.group(2)}")


def antes(h, tope):
    return h is not None and h <= tope


def despues(h, suelo):
    return h is not None and h >= suelo


def click_robusto(page, selector, timeout=8000):
    loc = page.locator(selector).first
    if not loc.count():
        raise RuntimeError(f"no existe {selector}")
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        loc.click(timeout=timeout)
        return "normal"
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        loc.click(timeout=4000)
        return "tras-escape"
    except Exception:
        pass
    try:
        loc.evaluate("e => e.click()")
        return "dom"
    except Exception as e:
        raise RuntimeError(f"{selector} no se dejo pinchar: {str(e)[:120]}")


def aceptar_cookies(page):
    try:
        page.click("#onetrust-accept-btn-handler", timeout=5000)
    except PWTimeout:
        pass


def elegir_fecha(page, iso):
    ms = ms_madrid(iso)
    bueno = (f".lightpick__day[data-time='{ms}']"
             ":not(.is-disabled):not(.is-previous-month):not(.is-next-month)")
    for _ in range(24):
        loc = page.locator(bueno).first
        try:
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                return
        except Exception:
            pass
        crudo = page.locator(f".lightpick__day[data-time='{ms}']").first
        try:
            if crudo.count() and "is-disabled" in (crudo.get_attribute("class") or ""):
                raise FueraDeVenta(f"{iso} aun no esta a la venta")
        except FueraDeVenta:
            raise
        except Exception:
            pass
        try:
            page.click(".lightpick__next-action", timeout=4000)
            page.wait_for_timeout(350)
        except Exception:
            break
    raise RuntimeError(f"no se encontro la celda de {iso} (data-time={ms})")


def cerrar_calendario(page):
    for sel in ["button:has-text('Aceptar')", "button:has-text('Listo')"]:
        try:
            page.click(sel, timeout=3000)
            page.wait_for_timeout(400)
            break
        except Exception:
            continue
    try:
        if page.locator(".lightpick").first.is_visible():
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
    except Exception:
        pass


def resumen_pasajeros(page):
    try:
        return (page.locator("#passengersSelection").first.get_attribute("value") or "").strip()
    except Exception:
        return ""


def poner_pasajeros(page, pax):
    try:
        click_robusto(page, "#passengersSelection", timeout=6000)
        page.wait_for_timeout(800)
    except Exception:
        return False, resumen_pasajeros(page)

    def pulsar(sel, veces):
        for _ in range(veces):
            try:
                loc = page.locator(sel).first
                if not (loc.count() and loc.is_visible()):
                    return False
                loc.click(timeout=3000)
                page.wait_for_timeout(350)
            except Exception:
                return False
        return True

    pulsar("[aria-label='Añadir adulto']", max(0, pax["adultos"] - 1))
    pulsar("[aria-label='Añadir niño mayor de 4']", pax["ninos"])

    for sel in ["button:has-text('Listo')", "[class*='passenger'] button:has-text('Listo')"]:
        try:
            page.click(sel, timeout=4000)
            break
        except Exception:
            continue
    else:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    page.wait_for_timeout(600)

    r = resumen_pasajeros(page).lower()
    ok = "2 adultos" in r and ("2 niños" in r or "2 ninos" in r)
    return ok, resumen_pasajeros(page)


def pagina_activa(page):
    """Renfe puede abrir los resultados en otra pestaña. Nos quedamos con la ultima."""
    try:
        paginas = [p for p in page.context.pages if not p.is_closed()]
        return paginas[-1] if paginas else page
    except Exception:
        return page


def leer_filas(page):
    """Devuelve la lista de textos de fila detectados por forma."""
    try:
        return page.evaluate(JS_FILAS) or []
    except Exception:
        return []


def buscar(page, cfg, ventana, destino):
    page.goto(RENFE, wait_until="domcontentloaded", timeout=45_000)
    aceptar_cookies(page)

    page.fill("#origin", cfg["origen"]["nombre"])
    page.wait_for_timeout(1100)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    page.fill("#destination", destino["nombre"])
    page.wait_for_timeout(1100)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    page.click("#first-input", timeout=8000)
    page.wait_for_timeout(900)
    elegir_fecha(page, ventana["salida"])
    page.wait_for_timeout(400)
    elegir_fecha(page, ventana["vuelta"])
    page.wait_for_timeout(400)
    cerrar_calendario(page)

    pax_ok, pax_txt = poner_pasajeros(page, cfg["pasajeros"])

    try:
        if page.locator("#passengersSelection[aria-expanded='true']").count():
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
    except Exception:
        pass

    click_robusto(page, "button:has-text('Buscar billete')", timeout=10_000)

    activa = pagina_activa(page)
    try:
        activa.wait_for_function(JS_HAY_FILAS, timeout=45_000)
    except PWTimeout:
        raise RuntimeError(
            f"tras buscar no aparecio ninguna fila con precio y hora. "
            f"url={activa.url[:120]}")

    return activa, pax_ok, pax_txt


def analizar(filas, sentido):
    horas, mejor = [], None
    for txt in filas:
        precio, salida = eur(txt), hhmm(txt)
        todas = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", txt)
        llegada = todas[1] if len(todas) > 1 else None
        tren = next((t for t in ("AVE", "AVLO", "AVANT", "ALVIA", "MD", "INTERCITY")
                     if t in txt.upper()), None)
        if salida:
            horas.append(salida)
        if precio is not None and (mejor is None or precio < mejor[0]):
            mejor = (precio, salida, llegada, tren)
    if mejor is None:
        raise RuntimeError(f"filas sin precio legible en {sentido}")
    return (*mejor, min(horas) if horas else None, max(horas) if horas else None)


def barrer_ruta(page, cfg, ventana, destino):
    activa, pax_ok, pax_txt = buscar(page, cfg, ventana, destino)

    filas_ida = leer_filas(activa)
    p_i, s_i, l_i, t_i, temp_i, tard_i = analizar(filas_ida, "ida")

    for sel in ["button:has-text('Continuar')", "button:has-text('Seleccionar')"]:
        try:
            activa.click(sel, timeout=6000)
            activa.wait_for_timeout(2000)
            break
        except Exception:
            continue

    filas_vta = leer_filas(activa)
    # Si la vuelta no se pudo abrir, al menos no inventamos: reutilizar la ida seria
    # duplicar el precio y cantar un chollo falso.
    if not filas_vta or filas_vta == filas_ida:
        raise RuntimeError("no se pudo leer el trayecto de vuelta por separado")
    p_v, s_v, l_v, t_v, temp_v, tard_v = analizar(filas_vta, "vuelta")

    hl = cfg["horario_limpio"]
    limpio = (antes(l_i, hl["ida_llegada_maxima"])
              and despues(s_v, hl["vuelta_salida_minima"])
              and antes(l_v, hl["vuelta_llegada_maxima"]))

    total = round(p_i + p_v, 2)
    ref = destino.get("referencia")
    return {
        "destino": destino["nombre"],
        "referencia": ref,
        "precio": total,
        "pasajeros_aplicados": pax_ok,
        "pasajeros_leidos": pax_txt,
        "aviso": None if pax_ok else
                 f"OJO: el buscador decia '{pax_txt}', no 2 adultos + 2 niños. "
                 "Precio NO comparable con la referencia.",
        "variacion_pct": (round((total - ref) / ref * 100, 1)
                          if (ref and pax_ok) else None),
        "ida": {"salida": s_i, "llegada": l_i, "tren": t_i},
        "vuelta": {"salida": s_v, "llegada": l_v, "tren": t_v},
        "mas_temprano": temp_i,
        "mas_tardio": tard_i or tard_v,
        "limpio": limpio,
        "error": None,
    }


def volcar(r):
    SALIDA.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    cfg = json.loads(RUTAS.read_text(encoding="utf-8"))
    DIAG.mkdir(exist_ok=True)

    res = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "alerta": "Barrido en curso, sin terminar.", "ventanas": {}}
    volcar(res)

    ok = fallos = fuera = total = seguidos = diags = sin_pax = 0
    rendido = False

    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(locale="es-ES", timezone_id="Europe/Madrid",
                              viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        page.set_default_timeout(T)

        for ventana in cfg["ventanas"]:
            filas = []
            for destino in cfg["destinos"]:
                if rendido:
                    filas.append({"destino": destino["nombre"], "precio": None,
                                  "error": "no intentado: barrido abortado antes"})
                    continue
                total += 1
                try:
                    fila = barrer_ruta(page, cfg, ventana, destino)
                    filas.append(fila)
                    ok += 1
                    seguidos = 0
                    if not fila["pasajeros_aplicados"]:
                        sin_pax += 1
                    print(f"[ok] {ventana['id']}-{destino['nombre']}: "
                          f"{fila['precio']} € ({fila['pasajeros_leidos']})")
                except FueraDeVenta as e:
                    fuera += 1
                    seguidos = 0
                    filas.append({"destino": destino["nombre"],
                                  "referencia": destino.get("referencia"),
                                  "precio": None, "error": str(e)})
                except Exception as e:
                    fallos += 1
                    seguidos += 1
                    etq = f"{ventana['id']}-{destino['nombre']}".replace(" ", "_")
                    if diags < MAX_DIAGNOSTICOS:
                        diags += 1
                        try:
                            act = pagina_activa(page)
                            act.screenshot(path=str(DIAG / f"{etq}.png"))
                            (DIAG / f"{etq}.html").write_text(
                                act.content()[:400_000], encoding="utf-8")
                            (DIAG / f"{etq}-filas.txt").write_text(
                                "\n---\n".join(leer_filas(act)[:40]) or "(ninguna)",
                                encoding="utf-8")
                        except Exception:
                            pass
                    print(f"[fallo {seguidos}/{FALLOS_SEGUIDOS_MAX}] {etq}: {e}",
                          file=sys.stderr)
                    filas.append({"destino": destino["nombre"],
                                  "referencia": destino.get("referencia"),
                                  "precio": None, "error": str(e)[:300]})
                    if seguidos >= FALLOS_SEGUIDOS_MAX:
                        rendido = True
                        print(f"\n{FALLOS_SEGUIDOS_MAX} fallos seguidos. Abortando.",
                              file=sys.stderr)

            res["ventanas"][ventana["id"]] = {
                "salida": ventana["salida"], "vuelta": ventana["vuelta"],
                "rutas": filas}
            volcar(res)

        nav.close()

    if rendido and ok == 0:
        res["alerta"] = ("El scraper no ha leido NINGUNA ruta y se ha rendido tras "
                         f"{FALLOS_SEGUIDOS_MAX} fallos seguidos. Ver diagnostico/.")
    elif rendido:
        res["alerta"] = (f"Barrido abortado tras {FALLOS_SEGUIDOS_MAX} fallos seguidos. "
                         f"{ok} rutas leidas antes.")
    elif sin_pax:
        res["alerta"] = (f"{sin_pax} rutas con pasajeros mal fijados: precios NO "
                         "comparables. Mirar pasajeros_leidos.")
    elif fallos:
        res["alerta"] = f"{fallos} de {total} rutas han fallado."
    else:
        res["alerta"] = None

    res["resumen"] = {"con_precio": ok, "fallidas": fallos, "fuera_de_venta": fuera,
                      "intentadas": total, "pasajeros_mal": sin_pax}
    res["generado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    volcar(res)
    print(f"\nHecho: {ok} con precio, {fuera} fuera de venta, {fallos} fallidas.")
    print(f"Alerta: {res['alerta']}")
    return 1 if (ok == 0 and fuera == 0) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        volcar({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "alerta": "El scraper reventó antes de empezar. Ver el log.",
                "ventanas": {}})
        sys.exit(1)
