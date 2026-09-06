#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe y escribe precios-trenes.json.

Version 3, ya con el DOM real de Renfe delante (sonda del 5 sep 2026).

Lo que la sonda enseno y aqui esta aplicado:
  - Renfe usa Lightpick. Las celdas son .lightpick__day con data-time en epoch
    MILISEGUNDOS Y EN HORA LOCAL DE MADRID, no UTC. Ese era el fallo: yo calculaba
    UTC y me pasaba una hora (dos en horario de verano), asi que no casaba nunca.
  - [data-date] no existe en Renfe. Fuera.
  - La cabecera del mes viene pegada: "Septiembre2026", sin espacio.
  - #origin, #destination, #first-input, .lightpick__next-action y
    button:has-text('Buscar billete') existen y son los buenos.

Y una distincion que antes no habia: una fecha que existe pero sale is-disabled
NO es una averia, es que Renfe todavia no la vende (abre a unos 4 meses). Eso no
cuenta para el contador de rendicion.
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

SEL = {
    "origen": ["#origin"],
    "destino": ["#destination"],
    "abrir_cal": ["#first-input", "input[placeholder*='Fecha']"],
    "siguiente": [".lightpick__next-action", "[class*='next-action']"],
    "cab_mes": [".lightpick__month-title"],
    "aceptar_cal": ["button:has-text('Aceptar')", "button:has-text('Continuar')"],
    "buscar": ["button:has-text('Buscar billete')", "button[type='submit']"],
    "filas": [".selectedTrain", ".trayecto", "[class*='train-item']",
              "[class*='trainList'] li"],
}


class FueraDeVenta(Exception):
    """La fecha existe en el calendario pero Renfe aun no la vende."""


def ms_madrid(iso):
    """Epoch en ms de la medianoche de esa fecha EN MADRID, que es lo que usa Lightpick."""
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, tzinfo=MADRID).timestamp() * 1000)


def primero_que_funcione(page, selectores, accion, *args, timeout=6000, **kw):
    ultimo = None
    for sel in selectores:
        try:
            getattr(page, accion)(sel, *args, timeout=timeout, **kw)
            return sel
        except Exception as e:
            ultimo = e
    raise RuntimeError(f"ninguno de {selectores} funciono ({str(ultimo)[:120]})")


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


def aceptar_cookies(page):
    try:
        page.click("#onetrust-accept-btn-handler", timeout=5000)
    except PWTimeout:
        pass


def elegir_fecha(page, iso):
    """
    Pincha el dia `iso` en el calendario Lightpick abierto.
    Avanza de mes hasta que la celda aparece. Distingue tres finales:
    la pincha, esta pero deshabilitada (fuera de venta), o no la encuentra.
    """
    ms = ms_madrid(iso)
    bueno = (f".lightpick__day[data-time='{ms}']"
             ":not(.is-disabled):not(.is-previous-month):not(.is-next-month)")
    cualquiera = f".lightpick__day[data-time='{ms}']"

    for _ in range(24):
        loc = page.locator(bueno).first
        try:
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                return
        except Exception:
            pass

        # ¿Existe pero deshabilitada? Entonces no es averia: aun no se vende.
        crudo = page.locator(cualquiera).first
        try:
            if crudo.count():
                clases = crudo.get_attribute("class") or ""
                if "is-disabled" in clases:
                    raise FueraDeVenta(f"{iso} aun no esta a la venta")
        except FueraDeVenta:
            raise
        except Exception:
            pass

        try:
            primero_que_funcione(page, SEL["siguiente"], "click", timeout=4000)
            page.wait_for_timeout(350)
        except Exception:
            break

    cab = ""
    try:
        cab = page.locator(SEL["cab_mes"][0]).first.inner_text()
    except Exception:
        pass
    raise RuntimeError(f"no se encontro la celda de {iso} (data-time={ms}); "
                       f"ultimo mes visible: '{cab.strip()}'")


def poner_pasajeros(page, pax):
    """
    Renfe arranca en '1 adulto'. Si no conseguimos cambiarlo, el precio seria de
    una sola persona y el radar compararia peras con manzanas. Por eso devolvemos
    si se logro, y el JSON lo deja escrito.
    """
    try:
        primero_que_funcione(
            page,
            ["[class*='passenger'] button", "button[class*='passenger']",
             "#passengersSelector", "[class*='passenger']"],
            "click", timeout=6000)
        page.wait_for_timeout(800)
    except Exception:
        return False

    def sumar(patrones, veces):
        for _ in range(veces):
            hecho = False
            for p in patrones:
                try:
                    loc = page.locator(p).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=2500)
                        page.wait_for_timeout(300)
                        hecho = True
                        break
                except Exception:
                    continue
            if not hecho:
                return False
        return True

    ok_adultos = sumar(["button[aria-label*='ñadir'][aria-label*='dulto']",
                        "[class*='adult'] [class*='plus']",
                        "[class*='adult'] button:has-text('+')"],
                       pax["adultos"] - 1)
    ok_ninos = sumar(["button[aria-label*='ñadir'][aria-label*='iño']",
                      "[class*='child'] [class*='plus']",
                      "[class*='child'] button:has-text('+')"],
                     pax["ninos"])

    try:
        primero_que_funcione(page, SEL["aceptar_cal"], "click", timeout=4000)
    except Exception:
        pass

    return bool(ok_adultos and ok_ninos)


def buscar(page, cfg, ventana, destino):
    page.goto(RENFE, wait_until="domcontentloaded", timeout=45_000)
    aceptar_cookies(page)

    primero_que_funcione(page, SEL["origen"], "fill", cfg["origen"]["nombre"])
    page.wait_for_timeout(1100)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    primero_que_funcione(page, SEL["destino"], "fill", destino["nombre"])
    page.wait_for_timeout(1100)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    primero_que_funcione(page, SEL["abrir_cal"], "click")
    page.wait_for_timeout(900)

    elegir_fecha(page, ventana["salida"])
    page.wait_for_timeout(400)
    elegir_fecha(page, ventana["vuelta"])
    page.wait_for_timeout(400)

    try:
        primero_que_funcione(page, SEL["aceptar_cal"], "click", timeout=5000)
    except Exception:
        pass

    pax_ok = poner_pasajeros(page, cfg["pasajeros"])

    primero_que_funcione(page, SEL["buscar"], "click", timeout=10_000)

    for sel in SEL["filas"]:
        try:
            page.wait_for_selector(sel, timeout=25_000)
            return pax_ok
        except PWTimeout:
            continue
    raise RuntimeError("la busqueda no devolvio ninguna lista de trenes")


def leer_trayecto(page, sentido):
    filas = []
    for sel in SEL["filas"]:
        filas = page.query_selector_all(sel)
        if filas:
            break
    if not filas:
        raise RuntimeError(f"sin filas de tren en {sentido}")

    horas, mejor = [], None
    for fila in filas:
        txt = fila.inner_text()
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
    pax_ok = buscar(page, cfg, ventana, destino)
    p_i, s_i, l_i, t_i, temp_i, tard_i = leer_trayecto(page, "ida")

    try:
        primero_que_funcione(
            page, ["button:has-text('Continuar')", "button:has-text('Seleccionar')"],
            "click", timeout=8000)
        page.wait_for_timeout(1500)
    except Exception:
        pass

    p_v, s_v, l_v, t_v, temp_v, tard_v = leer_trayecto(page, "vuelta")

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
        "aviso": None if pax_ok else
                 "PRECIO DE 1 ADULTO: no se pudo fijar 2+2. No comparar con la referencia.",
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

    ok = fallos = fuera = total = seguidos = diags = 0
    sin_pax = 0
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

                except FueraDeVenta as e:
                    # No es fallo: Renfe abre a ~4 meses. No cuenta para rendirse.
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
                            page.screenshot(path=str(DIAG / f"{etq}.png"))
                            (DIAG / f"{etq}.html").write_text(
                                page.content()[:400_000], encoding="utf-8")
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
                         f"{FALLOS_SEGUIDOS_MAX} fallos seguidos. Ver diagnostico/ "
                         "y lanzar la sonda.")
    elif rendido:
        res["alerta"] = (f"Barrido abortado tras {FALLOS_SEGUIDOS_MAX} fallos seguidos. "
                         f"{ok} rutas leidas antes de abortar.")
    elif sin_pax:
        res["alerta"] = (f"{sin_pax} rutas con precio de 1 adulto en vez de 2+2: "
                         "no se pudo fijar el selector de pasajeros. NO comparar "
                         "esos precios con la referencia.")
    elif fallos:
        res["alerta"] = f"{fallos} de {total} rutas han fallado."
    else:
        res["alerta"] = None

    res["resumen"] = {"con_precio": ok, "fallidas": fallos,
                      "fuera_de_venta": fuera, "intentadas": total}
    res["generado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    volcar(res)
    print(f"\nHecho: {ok} con precio, {fuera} fuera de venta, {fallos} fallidas.")
    print(f"Alerta: {res['alerta']}")
    return 1 if ok == 0 and fuera == 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        volcar({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "alerta": "El scraper reventó antes de empezar. Ver el log.",
                "ventanas": {}})
        sys.exit(1)
