#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe y escribe precios-trenes.json.

Version 6. Dos cambios de fondo: se matan las animaciones y se hacen DOS
busquedas de solo ida en vez de una de ida y vuelta.

Historial, para no repetir errores:
  v1  Insistia 63 veces con el mismo fallo y agotaba los 45 min del job.
      -> Se rinde a los 3 fallos seguidos. RESUELTO.
  v2  data-time en UTC; Lightpick lo guarda en hora LOCAL DE MADRID.
      -> zoneinfo("Europe/Madrid"). RESUELTO Y CONFIRMADO.
  v3  Cerraba el panel de pasajeros con "Aceptar"; se llama "Listo". El panel
      tapaba el boton de buscar. -> RESUELTO Y CONFIRMADO.
  v4  Buscaba las filas por clases inventadas. -> Deteccion por forma. RESUELTO:
      la v5 ya leia filas con precio y hora correctamente.
  v5  El flujo de ida y vuelta devolvia el mismo listado dos veces, asi que la
      salvaguarda (no inventar la vuelta) abortaba. Y el carrusel de la portada
      hacia que Playwright nunca diera los elementos por estables:
      "waiting for element to be stable".
      -> v6: animaciones desactivadas por CSS, y dos busquedas de SOLO IDA
         que se suman. Un listado por busqueda, sin flujo de dos pasos.

NOTA DE METODO: dos billetes de ida pueden salir algo mas caros que un ida y
vuelta con descuento de Renfe. Se marca en el JSON como metodo "dos_idas" para
no comparar peras con manzanas sin saberlo. Preferimos un numero honesto y
reproducible a uno mas bajo que no sabemos leer.

DIAGNOSTICO: cuando algo falla, las filas detectadas se IMPRIMEN EN EL LOG, no
solo en el artefacto. El log es el canal que de verdad llega.
"""

import json
import os
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
LIMITE = int(os.environ.get("LIMITE", "0"))   # 0 = todos los destinos

# Sin esto, el carrusel de la portada gira eternamente y Playwright nunca da un
# elemento por "estable". Fue la causa de "waiting for element to be stable".
CSS_SIN_ANIMACION = """
*, *::before, *::after {
  animation: none !important;
  transition: none !important;
  scroll-behavior: auto !important;
}
"""

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

JS_SOLO_IDA = r"""
() => {
  const els = document.querySelectorAll('label,button,span,div,a,input');
  for (const e of els) {
    const t = ((e.innerText || e.value || '') + '').trim().toLowerCase();
    if (t === 'viaje solo ida' || t === 'solo ida' || t === 'sólo ida') {
      e.click();
      return true;
    }
  }
  return false;
}
"""


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
        loc.scroll_into_view_if_needed(timeout=2500)
    except Exception:
        pass
    for intento in ("normal", "escape", "dom"):
        try:
            if intento == "normal":
                loc.click(timeout=timeout)
                return "normal"
            if intento == "escape":
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                loc.click(timeout=4000)
                return "tras-escape"
            loc.evaluate("e => e.click()")
            return "dom"
        except Exception:
            continue
    raise RuntimeError(f"{selector} no se dejo pinchar de ninguna forma")


def aceptar_cookies(page):
    try:
        page.click("#onetrust-accept-btn-handler", timeout=5000)
    except PWTimeout:
        pass


def poner_solo_ida(page):
    """Renfe arranca en ida y vuelta. Lo pasamos a solo ida."""
    for sel in ["label:has-text('Viaje solo ida')", "label:has-text('Solo ida')",
                "button:has-text('Viaje solo ida')"]:
        try:
            page.click(sel, timeout=2500)
            page.wait_for_timeout(500)
            return True
        except Exception:
            continue
    try:
        if page.evaluate(JS_SOLO_IDA):
            page.wait_for_timeout(500)
            return True
    except Exception:
        pass
    return False


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
            page.wait_for_timeout(300)
        except Exception:
            break
    raise RuntimeError(f"no se encontro la celda de {iso} (data-time={ms})")


def cerrar_paneles(page):
    for sel in ["button:has-text('Aceptar')", "button:has-text('Listo')"]:
        try:
            page.click(sel, timeout=2500)
            page.wait_for_timeout(300)
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def resumen_pasajeros(page):
    try:
        return (page.locator("#passengersSelection").first
                .get_attribute("value") or "").strip()
    except Exception:
        return ""


def poner_pasajeros(page, pax):
    try:
        click_robusto(page, "#passengersSelection", timeout=6000)
        page.wait_for_timeout(700)
    except Exception:
        return False, resumen_pasajeros(page)

    def pulsar(sel, veces):
        for _ in range(veces):
            try:
                loc = page.locator(sel).first
                if not (loc.count() and loc.is_visible()):
                    return
                loc.click(timeout=3000)
                page.wait_for_timeout(300)
            except Exception:
                return

    pulsar("[aria-label='Añadir adulto']", max(0, pax["adultos"] - 1))
    pulsar("[aria-label='Añadir niño mayor de 4']", pax["ninos"])

    cerrado = False
    for sel in ["button:has-text('Listo')"]:
        try:
            page.click(sel, timeout=4000)
            cerrado = True
        except Exception:
            pass
    if not cerrado:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    page.wait_for_timeout(500)

    r = resumen_pasajeros(page)
    rl = r.lower()
    ok = "2 adultos" in rl and ("2 niños" in rl or "2 ninos" in rl)
    return ok, r


def buscar_un_sentido(page, origen, destino, fecha, pax):
    """Una busqueda de SOLO IDA. Devuelve (filas, pax_ok, pax_txt)."""
    page.goto(RENFE, wait_until="domcontentloaded", timeout=45_000)
    aceptar_cookies(page)
    page.wait_for_timeout(600)

    poner_solo_ida(page)

    page.fill("#origin", origen)
    page.wait_for_timeout(1000)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    page.fill("#destination", destino)
    page.wait_for_timeout(1000)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    click_robusto(page, "#first-input", timeout=8000)
    page.wait_for_timeout(800)
    elegir_fecha(page, fecha)
    page.wait_for_timeout(400)
    cerrar_paneles(page)

    pax_ok, pax_txt = poner_pasajeros(page, pax)
    cerrar_paneles(page)

    click_robusto(page, "button:has-text('Buscar billete')", timeout=10_000)

    activa = [p for p in page.context.pages if not p.is_closed()][-1]
    try:
        activa.wait_for_function(JS_HAY_FILAS, timeout=45_000)
    except PWTimeout:
        raise RuntimeError(f"sin filas tras buscar {origen}->{destino} {fecha}; "
                           f"url={activa.url[:100]}")

    return (activa.evaluate(JS_FILAS) or []), pax_ok, pax_txt


def analizar(filas, etiqueta):
    """De la lista de textos de fila saca el mas barato y las horas extremas."""
    horas, mejor, crudo = [], None, None
    for txt in filas:
        precio = eur(txt)
        todas = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", txt)
        salida = todas[0] if todas else None
        llegada = todas[1] if len(todas) > 1 else None
        tren = next((t for t in ("AVE", "AVLO", "AVANT", "ALVIA", "MD", "INTERCITY")
                     if t in txt.upper()), None)
        if salida:
            horas.append(salida)
        if precio is not None and (mejor is None or precio < mejor[0]):
            mejor, crudo = (precio, salida, llegada, tren), txt
    if mejor is None:
        raise RuntimeError(f"filas sin precio legible en {etiqueta}")
    return (*mejor, min(horas) if horas else None,
            max(horas) if horas else None, crudo)


def barrer_ruta(page, cfg, ventana, destino):
    origen = cfg["origen"]["nombre"]
    pax = cfg["pasajeros"]

    f_ida, pax_ok1, pax1 = buscar_un_sentido(page, origen, destino["nombre"],
                                             ventana["salida"], pax)
    p_i, s_i, l_i, t_i, temp_i, tard_i, crudo_i = analizar(f_ida, "ida")

    f_vta, pax_ok2, pax2 = buscar_un_sentido(page, destino["nombre"], origen,
                                             ventana["vuelta"], pax)
    p_v, s_v, l_v, t_v, temp_v, tard_v, crudo_v = analizar(f_vta, "vuelta")

    pax_ok = pax_ok1 and pax_ok2
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
        "metodo": "dos_idas",
        "pasajeros_aplicados": pax_ok,
        "pasajeros_leidos": f"{pax1} / {pax2}",
        "aviso": None if pax_ok else
                 f"OJO: el buscador decia '{pax1}' y '{pax2}', no 2 adultos + 2 niños. "
                 "Precio NO comparable con la referencia.",
        "variacion_pct": (round((total - ref) / ref * 100, 1)
                          if (ref and pax_ok) else None),
        "ida": {"salida": s_i, "llegada": l_i, "tren": t_i, "precio": p_i},
        "vuelta": {"salida": s_v, "llegada": l_v, "tren": t_v, "precio": p_v},
        "mas_temprano": temp_i,
        "mas_tardio": tard_v,
        "limpio": limpio,
        "fila_cruda_ida": (crudo_i or "")[:200],
        "fila_cruda_vuelta": (crudo_v or "")[:200],
        "error": None,
    }


def volcar(r):
    SALIDA.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    cfg = json.loads(RUTAS.read_text(encoding="utf-8"))
    DIAG.mkdir(exist_ok=True)
    destinos = cfg["destinos"][:LIMITE] if LIMITE else cfg["destinos"]
    if LIMITE:
        print(f"LIMITE={LIMITE}: solo {len(destinos)} destinos (modo validacion)")

    res = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "alerta": "Barrido en curso, sin terminar.", "ventanas": {}}
    volcar(res)

    ok = fallos = fuera = total = seguidos = diags = sin_pax = 0
    rendido = False

    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(locale="es-ES", timezone_id="Europe/Madrid",
                              reduced_motion="reduce",
                              viewport={"width": 1440, "height": 950})
        ctx.add_init_script(
            "document.addEventListener('DOMContentLoaded', () => {"
            "  const s = document.createElement('style');"
            f"  s.textContent = {json.dumps(CSS_SIN_ANIMACION)};"
            "  document.head.appendChild(s);"
            "});")
        page = ctx.new_page()
        page.set_default_timeout(T)

        for ventana in cfg["ventanas"]:
            filas = []
            for destino in destinos:
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
                    print(f"[ok] {ventana['id']}-{destino['nombre']}: {fila['precio']} € "
                          f"(ida {fila['ida']['precio']} + vuelta {fila['vuelta']['precio']}) "
                          f"| pax: {fila['pasajeros_leidos']}")
                    print(f"      fila ida: {fila['fila_cruda_ida'][:120]}")

                except FueraDeVenta as e:
                    fuera += 1
                    seguidos = 0
                    filas.append({"destino": destino["nombre"],
                                  "referencia": destino.get("referencia"),
                                  "precio": None, "error": str(e)})
                    print(f"[fuera de venta] {ventana['id']}-{destino['nombre']}")

                except Exception as e:
                    fallos += 1
                    seguidos += 1
                    etq = f"{ventana['id']}-{destino['nombre']}".replace(" ", "_")
                    print(f"[fallo {seguidos}/{FALLOS_SEGUIDOS_MAX}] {etq}: {e}",
                          file=sys.stderr)

                    # Lo que de verdad hace falta para arreglarlo: al LOG, no solo
                    # al artefacto, que es lo que acaba llegandome.
                    if diags < MAX_DIAGNOSTICOS:
                        diags += 1
                        try:
                            act = [p for p in ctx.pages if not p.is_closed()][-1]
                            vistas = act.evaluate(JS_FILAS) or []
                            print(f"      url: {act.url[:140]}", file=sys.stderr)
                            print(f"      filas detectadas: {len(vistas)}", file=sys.stderr)
                            for v in vistas[:12]:
                                print(f"        · {v[:180]}", file=sys.stderr)
                            act.screenshot(path=str(DIAG / f"{etq}.png"))
                            (DIAG / f"{etq}.html").write_text(
                                act.content()[:400_000], encoding="utf-8")
                        except Exception as e2:
                            print(f"      (no se pudo volcar: {str(e2)[:120]})",
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
                         f"{FALLOS_SEGUIDOS_MAX} fallos seguidos. Ver el log.")
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
                      "intentadas": total, "pasajeros_mal": sin_pax,
                      "metodo": "dos billetes de ida sumados"}
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
