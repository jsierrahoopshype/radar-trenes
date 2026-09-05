#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe y escribe precios-trenes.json.

Cambios respecto a la primera version, tras el fallo del 5 sep:
  - SE RINDE A LA TERCERA. Antes insistia 63 veces con el mismo error y se comia
    los 45 minutos del job. Ahora, si fallan 3 rutas seguidas, aborta y lo dice.
  - Elige la fecha probando varias estrategias, y si ninguna casa, navega el
    calendario por texto (leer cabecera de mes, pulsar siguiente, pinchar el dia).
    Eso no depende de como Renfe llame a sus clases.
  - Escribe el JSON AL EMPEZAR, no solo al acabar, para que una cancelacion no
    deje al radar sin fichero.
  - Diagnostico pequeno: capturas de pantalla visible, no de pagina completa, y
    solo de los 3 primeros fallos. El zip pasa de 204 MB a menos de 2 MB.
"""

import json
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
T = 20_000                 # timeout por accion: corto a proposito
FALLOS_SEGUIDOS_MAX = 3    # a la tercera nos rendimos
MAX_DIAGNOSTICOS = 3

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

SEL = {
    "origen": ["#origin", "#origin-input", "input[name='origin']",
               "input[placeholder*='Origen']"],
    "destino": ["#destination", "#destination-input", "input[name='destination']",
                "input[placeholder*='Destino']"],
    "abrir_cal": [".rf-daterange__input", "#first-input", ".lightpick__input",
                  "input[placeholder*='Fecha']"],
    "cab_mes": [".lightpick__month-title", "[class*='month-title']",
                "[class*='monthName']", "[class*='month']"],
    "siguiente": [".lightpick__next-action", "button[aria-label*='iguiente']",
                  "[class*='next-action']", "[class*='next']"],
    "aceptar_cal": ["button:has-text('Aceptar')", "button:has-text('Continuar')"],
    "buscar": ["button:has-text('Buscar billete')", "button:has-text('Buscar')",
               "[class*='btn-search']"],
    "filas": [".selectedTrain", ".trayecto", "[class*='train-item']",
              "[class*='trainList'] li"],
}


class Rendicion(Exception):
    """Han fallado demasiadas rutas seguidas: no tiene sentido seguir."""


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
    for sel in ["#onetrust-accept-btn-handler",
                "button:has-text('Aceptar todas')", "button:has-text('Aceptar')"]:
        try:
            page.click(sel, timeout=4000)
            return
        except PWTimeout:
            continue


def elegir_fecha(page, iso):
    """
    Pincha el dia `iso` (YYYY-MM-DD) en el calendario abierto.
    Cuatro estrategias, de la mas barata a la mas tozuda.
    """
    y, mes, dia = map(int, iso.split("-"))

    # 1) atributo directo, por si Renfe lo pone facil
    for sel in (f"[data-date='{iso}']", f"[data-fecha='{iso}']",
                f"td[data-date='{iso}']", f"[aria-label='{iso}']"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                return
        except Exception:
            pass

    # 2) lightpick guarda la fecha como epoch en milisegundos
    for tz in ("UTC", "Europe/Madrid"):
        try:
            ms = int(datetime(y, mes, dia, tzinfo=timezone.utc).timestamp() * 1000)
            loc = page.locator(f".lightpick__day[data-time='{ms}']").first
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                return
        except Exception:
            pass

    # 3) aria-label en castellano: "6 de noviembre de 2026"
    etiqueta = f"{dia} de {MESES[mes - 1]} de {y}"
    for sel in (f"[aria-label*='{etiqueta}']", f"[title*='{etiqueta}']"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                return
        except Exception:
            pass

    # 4) a la brava: leer la cabecera del mes y avanzar hasta llegar
    objetivo = f"{MESES[mes - 1]} {y}"
    for _ in range(24):                       # como mucho dos anos hacia delante
        cabecera = ""
        for sel in SEL["cab_mes"]:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    cabecera = (loc.inner_text() or "").strip().lower()
                    break
            except Exception:
                continue

        if objetivo in cabecera:
            # dentro del mes correcto, pinchamos la celda cuyo texto sea el dia
            for sel in (".lightpick__day:not(.is-disabled)",
                        "td:not(.disabled):not(.is-disabled)",
                        "[class*='day']:not([class*='disabled'])"):
                try:
                    celdas = page.locator(sel)
                    for i in range(celdas.count()):
                        c = celdas.nth(i)
                        if (c.inner_text() or "").strip() == str(dia) and c.is_visible():
                            c.click(timeout=4000)
                            return
                except Exception:
                    continue
            raise RuntimeError(f"mes {objetivo} localizado pero sin celda para el dia {dia}")

        try:
            primero_que_funcione(page, SEL["siguiente"], "click", timeout=4000)
            page.wait_for_timeout(400)
        except Exception:
            break

    raise RuntimeError(f"no se pudo seleccionar la fecha {iso} "
                       f"(ultima cabecera vista: '{cabecera or 'ninguna'}')")


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
    page.wait_for_timeout(500)
    elegir_fecha(page, ventana["vuelta"])
    page.wait_for_timeout(500)

    try:
        primero_que_funcione(page, SEL["aceptar_cal"], "click", timeout=5000)
    except Exception:
        pass

    primero_que_funcione(page, SEL["buscar"], "click", timeout=10_000)

    for sel in SEL["filas"]:
        try:
            page.wait_for_selector(sel, timeout=25_000)
            return
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
    buscar(page, cfg, ventana, destino)
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
        "variacion_pct": round((total - ref) / ref * 100, 1) if ref else None,
        "ida": {"salida": s_i, "llegada": l_i, "tren": t_i},
        "vuelta": {"salida": s_v, "llegada": l_v, "tren": t_v},
        "mas_temprano": temp_i,
        "mas_tardio": tard_i or tard_v,
        "limpio": limpio,
        "error": None,
    }


def volcar(resultado):
    SALIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def main():
    cfg = json.loads(RUTAS.read_text(encoding="utf-8"))
    DIAG.mkdir(exist_ok=True)

    resultado = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alerta": "Barrido en curso, sin terminar.",
        "ventanas": {},
    }
    volcar(resultado)          # el fichero existe desde el primer segundo

    fallos = ok = total = 0
    seguidos = 0
    diagnosticos = 0
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
                    filas.append(barrer_ruta(page, cfg, ventana, destino))
                    ok += 1
                    seguidos = 0
                except Exception as e:
                    fallos += 1
                    seguidos += 1
                    etq = f"{ventana['id']}-{destino['nombre']}".replace(" ", "_")
                    if diagnosticos < MAX_DIAGNOSTICOS:
                        diagnosticos += 1
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
                        print(f"\n{FALLOS_SEGUIDOS_MAX} fallos seguidos. Abortando "
                              f"para no gastar 45 minutos repitiendo el mismo error.",
                              file=sys.stderr)

            resultado["ventanas"][ventana["id"]] = {
                "salida": ventana["salida"], "vuelta": ventana["vuelta"],
                "rutas": filas,
            }
            volcar(resultado)      # se actualiza ventana a ventana

        nav.close()

    if rendido and ok == 0:
        resultado["alerta"] = (
            f"El scraper no ha podido leer NINGUNA ruta y se ha rendido tras "
            f"{FALLOS_SEGUIDOS_MAX} fallos seguidos. Renfe ha cambiado el maquetado "
            f"o esta bloqueando al runner. Mirar diagnostico/ y lanzar la sonda."
        )
    elif rendido:
        resultado["alerta"] = (
            f"Barrido abortado tras {FALLOS_SEGUIDOS_MAX} fallos seguidos. "
            f"{ok} rutas se leyeron antes de abortar."
        )
    elif fallos:
        resultado["alerta"] = f"{fallos} de {total} rutas han fallado."
    else:
        resultado["alerta"] = None

    for v in resultado["ventanas"].values():
        dias = (datetime.fromisoformat(v["salida"]).date()
                - datetime.now(timezone.utc).date()).days
        if dias > 125 and all(r.get("precio") is None for r in v["rutas"]):
            for r in v["rutas"]:
                r["error"] = "fuera de venta todavia (Renfe abre a ~4 meses)"

    resultado["generado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    volcar(resultado)
    print(f"\nHecho: {ok}/{total} rutas con precio. Alerta: {resultado['alerta']}")
    return 1 if ok == 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        volcar({
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "alerta": "El scraper reventó antes de empezar. Ver el log del workflow.",
            "ventanas": {},
        })
        sys.exit(1)
