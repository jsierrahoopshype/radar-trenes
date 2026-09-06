#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe y escribe precios-trenes.json.

Version 9.

Historial, para no repetir errores:
  v1  Insistia 63 veces con el mismo fallo. -> Se rinde a los 3. RESUELTO.
  v2  data-time en UTC; Lightpick usa hora LOCAL DE MADRID. RESUELTO.
  v3  Cerraba el panel de pasajeros con "Aceptar"; se llama "Listo". RESUELTO.
  v4  Filas por clases inventadas. -> Deteccion por forma. RESUELTO.
  v5  Flujo de ida y vuelta devolvia el mismo listado dos veces.
  v6  Al pasar a solo ida, #first-input desaparece. Rompi algo que funcionaba.
  v7  Calendario por bateria + verificacion. FUNCIONO: 5 rutas leidas.
      Pero el barrido destapo tres cosas:
        a) Los pasajeros SE ACUMULABAN entre busquedas (Renfe los recuerda y yo
           sumaba encima): 2+2, luego 3+4, luego 4+5.
        b) El precio de la parrilla es "Precio DESDE X €", POR PERSONA Y
           TRAYECTO, no el total de los cuatro. Compararlo contra una referencia
           de 4 personas daba -74,6 % en Cuenca: un chollo fantasma puro.
        c) Febrero devolvia parrilla sin trenes (aun no esta a la venta) y eso
           se contaba como averia, abortando el barrido entero.
  v8  Delta de pasajeros (bien) + unidades por persona (bien) + SinTrenes (bien),
      PERO los pasajeros seguian saliendo "1 adulto" en la primera ventana: el
      web component no esta hidratado en la primera carga y los clics se pierden.
  v9  Se espera a que exista el boton de sumar antes de pulsarlo, y se verifica
      con hasta 3 reintentos. Nunca se da por bueno sin releer.
      a) Los pasajeros se fijan por DELTA sobre lo que ya hay, no sumando.
      b) UNIDADES: se compara precio por persona contra referencia/4. Sin
         inventar nada. Nunca se compara un "desde" con un total de cuatro.
      c) Parrilla sin trenes = SinTrenes: ni cuenta como fallo ni aborta, y si
         una ventana da dos seguidas se salta entera (no esta a la venta).

REGLA APRENDIDA, aplicada ya en cuatro sitios: no depender de nombres (ids,
clases) sino de funcion verificable. Filas = "lo que tiene precio y hora".
Calendario = "lo que hace aparecer .lightpick". Pasajeros = "lo que deja el
value del desplegable en 2 adultos, 2 niños". Cada vez que me la he saltado,
he fallado.

QUE MIDE ESTE FICHERO: precio "desde" POR PERSONA Y TRAYECTO. El campo
precio_por_persona es la suma ida + vuelta por persona. La comparacion se hace
contra referencia/4 porque la tabla de referencia son totales de 4 personas.
precio_estimado_4 es orientativo (x4) y los niños suelen pagar menos, asi que
tiende a quedarse alto. No usar ese estimado para decidir nada.
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
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
SIN_TRENES_PARA_SALTAR_VENTANA = 2
MAX_DIAGNOSTICOS = 3
LIMITE = int(os.environ.get("LIMITE", "0"))

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

# Marcadores de "hemos llegado a la parrilla de resultados", vistos en el log del
# 6 sep: botones day_cell / move_to_tomorrow y la url de venta.
JS_ES_PARRILLA = r"""
() => !!(document.querySelector('#day_button, .day_cell, [class*="move_to_"]')
         || /buscarTren|venta\.renfe/i.test(location.href));
"""

JS_SOLO_IDA = r"""
() => {
  for (const e of document.querySelectorAll('label,button,span,div,a,input')) {
    const t = ((e.innerText || e.value || '') + '').trim().toLowerCase();
    if (t === 'viaje solo ida' || t === 'solo ida' || t === 'sólo ida') {
      e.click(); return true;
    }
  }
  return false;
}
"""

JS_ABRIR_FECHA = r"""
() => {
  for (const e of document.querySelectorAll('input,button')) {
    const s = ((e.placeholder || '') + ' ' + (e.getAttribute('aria-label') || '')
               + ' ' + (e.value || '')).toLowerCase();
    if (s.includes('fecha') || s.includes('ida')) { e.click(); return true; }
  }
  return false;
}
"""

JS_INPUTS = r"""
() => Array.from(document.querySelectorAll('input,button'))
  .filter(e => e.offsetParent || e.getClientRects().length)
  .slice(0, 60)
  .map(e => `${e.tagName}#${e.id || '-'} ph='${e.placeholder || ''}' `
            + `val='${(e.value || '').slice(0, 30)}' cls='`
            + `${(typeof e.className === 'string' ? e.className : '').slice(0, 60)}'`);
"""


class FueraDeVenta(Exception):
    pass


class SinTrenes(Exception):
    """Llegamos a la parrilla pero no hay trenes: fuera de venta o sin servicio."""


def ms_madrid(iso):
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, tzinfo=MADRID).timestamp() * 1000)


def mas_dias(iso, n):
    return (datetime.fromisoformat(iso) + timedelta(days=n)).strftime("%Y-%m-%d")


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
    for modo in ("normal", "escape", "dom"):
        try:
            if modo == "normal":
                loc.click(timeout=timeout)
            elif modo == "escape":
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                loc.click(timeout=4000)
            else:
                loc.evaluate("e => e.click()")
            return modo
        except Exception:
            continue
    raise RuntimeError(f"{selector} no se dejo pinchar")


def calendario_visible(page):
    try:
        loc = page.locator(".lightpick").first
        return bool(loc.count() and loc.is_visible())
    except Exception:
        return False


def abrir_calendario(page):
    if calendario_visible(page):
        return "ya-abierto"
    for sel in ["#first-input", "input[placeholder*='Fecha']",
                "input[placeholder*='fecha']", "[class*='daterange'] input",
                ".lightpick__input", "#second-input", "[class*='daterange']"]:
        try:
            if not page.locator(sel).first.count():
                continue
            click_robusto(page, sel, timeout=5000)
            page.wait_for_timeout(700)
            if calendario_visible(page):
                return sel
        except Exception:
            continue
    try:
        if page.evaluate(JS_ABRIR_FECHA):
            page.wait_for_timeout(700)
            if calendario_visible(page):
                return "js"
    except Exception:
        pass
    raise RuntimeError("no se pudo abrir el calendario (.lightpick nunca aparecio)")


def aceptar_cookies(page):
    try:
        page.click("#onetrust-accept-btn-handler", timeout=5000)
    except PWTimeout:
        pass


def poner_solo_ida(page):
    for sel in ["label:has-text('Viaje solo ida')", "label:has-text('Solo ida')",
                "button:has-text('Viaje solo ida')"]:
        try:
            page.click(sel, timeout=2500)
            page.wait_for_timeout(600)
            return True
        except Exception:
            continue
    try:
        if page.evaluate(JS_SOLO_IDA):
            page.wait_for_timeout(600)
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
            page.click(sel, timeout=2000)
            page.wait_for_timeout(300)
        except Exception:
            continue
    if calendario_visible(page):
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


def contar_pasajeros(texto):
    """De '2 adultos, 2 niños' saca (2, 2). Por defecto Renfe arranca en 1 adulto."""
    t = (texto or "").lower()
    a = re.search(r"(\d+)\s*adulto", t)
    n = re.search(r"(\d+)\s*ni[ñn]o", t)
    return (int(a.group(1)) if a else 1, int(n.group(1)) if n else 0)


def poner_pasajeros(page, pax):
    """
    Deja el buscador en 2 adultos + 2 ninos y lo VERIFICA, con reintentos.

    Fallo de la v8 (6 sep): en noviembre salia "1 adulto" y en diciembre
    "2 adultos, 2 ninos". La asimetria delataba una carrera:
    <rf-passengers-integration> es un web component y en la primera carga del
    navegador tarda en hidratarse, asi que los clics caian en el vacio. Diciembre
    "funcionaba" solo porque Renfe recordaba el 2+2 de antes y el atajo de
    "ya esta correcto" devolvia True sin tocar nada: heredado, no logrado.

    Ahora: se espera a que el boton de sumar EXISTA antes de pulsarlo, y se
    reintenta hasta 3 veces comprobando el resultado. Nunca se da por bueno un
    ajuste sin releerlo.
    """
    objetivo = (pax["adultos"], pax["ninos"])

    for intento in range(3):
        txt = resumen_pasajeros(page)
        if contar_pasajeros(txt) == objetivo:
            return True, txt

        try:
            click_robusto(page, "#passengersSelection", timeout=6000)
            # Hidratacion: hasta que este boton no existe, los clics no cuentan.
            page.wait_for_selector("[aria-label='Añadir adulto']", timeout=8000)
            page.wait_for_timeout(400)
        except Exception:
            page.wait_for_timeout(1000)
            continue

        a_hay, n_hay = contar_pasajeros(resumen_pasajeros(page))
        a_qui, n_qui = objetivo

        def pulsar(sel, veces):
            for _ in range(max(0, veces)):
                try:
                    loc = page.locator(sel).first
                    if not (loc.count() and loc.is_visible()):
                        return
                    loc.click(timeout=3000)
                    page.wait_for_timeout(350)
                except Exception:
                    return

        if a_qui > a_hay:
            pulsar("[aria-label='Añadir adulto']", a_qui - a_hay)
        elif a_qui < a_hay:
            pulsar("[aria-label='Eliminar adulto']", a_hay - a_qui)

        if n_qui > n_hay:
            pulsar("[aria-label='Añadir niño mayor de 4']", n_qui - n_hay)
        elif n_qui < n_hay:
            pulsar("[aria-label='Eliminar niño mayor de 4']", n_hay - n_qui)

        try:
            page.click("button:has-text('Listo')", timeout=4000)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        page.wait_for_timeout(600)

        if contar_pasajeros(resumen_pasajeros(page)) == objetivo:
            return True, resumen_pasajeros(page)

    final = resumen_pasajeros(page)
    return contar_pasajeros(final) == objetivo, final


def buscar_un_sentido(page, origen, destino, fecha, pax):
    page.goto(RENFE, wait_until="domcontentloaded", timeout=45_000)
    aceptar_cookies(page)
    page.wait_for_timeout(600)

    solo_ida = poner_solo_ida(page)

    page.fill("#origin", origen)
    page.wait_for_timeout(1000)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    page.fill("#destination", destino)
    page.wait_for_timeout(1000)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    abrir_calendario(page)
    elegir_fecha(page, fecha)
    page.wait_for_timeout(400)
    if not solo_ida:
        try:
            elegir_fecha(page, mas_dias(fecha, 3))
            page.wait_for_timeout(400)
        except Exception:
            pass

    cerrar_paneles(page)
    pax_ok, pax_txt = poner_pasajeros(page, pax)
    cerrar_paneles(page)

    click_robusto(page, "button:has-text('Buscar billete')", timeout=10_000)

    activa = [p for p in page.context.pages if not p.is_closed()][-1]
    try:
        activa.wait_for_function(JS_HAY_FILAS, timeout=30_000)
    except PWTimeout:
        # Distinguir "la parrilla existe pero esta vacia" de "no llegamos".
        try:
            en_parrilla = bool(activa.evaluate(JS_ES_PARRILLA))
        except Exception:
            en_parrilla = False
        if en_parrilla:
            raise SinTrenes(f"parrilla sin trenes para {origen}->{destino} {fecha} "
                            "(fuera de venta o sin servicio ese dia)")
        raise RuntimeError(f"no se llego a la parrilla en {origen}->{destino} {fecha}; "
                           f"url={activa.url[:100]}")

    metodo = "solo_ida" if solo_ida else "ida_de_ida_y_vuelta"
    return (activa.evaluate(JS_FILAS) or []), pax_ok, pax_txt, metodo


def analizar(filas, etiqueta):
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
    n_pax = pax["adultos"] + pax["ninos"]

    f_i, ok1, pax1, met1 = buscar_un_sentido(page, origen, destino["nombre"],
                                             ventana["salida"], pax)
    p_i, s_i, l_i, t_i, temp_i, _, crudo_i = analizar(f_i, "ida")

    f_v, ok2, pax2, met2 = buscar_un_sentido(page, destino["nombre"], origen,
                                             ventana["vuelta"], pax)
    p_v, s_v, l_v, t_v, _, tard_v, crudo_v = analizar(f_v, "vuelta")

    pax_ok = ok1 and ok2
    hl = cfg["horario_limpio"]
    limpio = (antes(l_i, hl["ida_llegada_maxima"])
              and despues(s_v, hl["vuelta_salida_minima"])
              and antes(l_v, hl["vuelta_llegada_maxima"]))

    # UNIDADES. Lo medido es "desde" POR PERSONA. La referencia es total de 4.
    # Se compara por persona contra referencia/4: mismas unidades, sin inventar.
    por_persona = round(p_i + p_v, 2)
    ref_total = destino.get("referencia")
    ref_persona = round(ref_total / n_pax, 2) if ref_total else None
    var = (round((por_persona - ref_persona) / ref_persona * 100, 1)
           if (ref_persona and pax_ok) else None)

    return {
        "destino": destino["nombre"],
        "precio_por_persona": por_persona,
        "precio_estimado_4": round(por_persona * n_pax, 2),
        "referencia_total_4": ref_total,
        "referencia_por_persona": ref_persona,
        "variacion_pct": var,
        "unidad": "precio 'desde' por persona, ida + vuelta",
        "metodo": f"{met1}+{met2}",
        "pasajeros_aplicados": pax_ok,
        "pasajeros_leidos": f"{pax1} / {pax2}",
        "aviso": None if pax_ok else
                 f"OJO: el buscador decia '{pax1}' y '{pax2}'. Precio no fiable.",
        "ida": {"salida": s_i, "llegada": l_i, "tren": t_i, "precio_persona": p_i},
        "vuelta": {"salida": s_v, "llegada": l_v, "tren": t_v, "precio_persona": p_v},
        "mas_temprano": temp_i,
        "mas_tardio": tard_v,
        "limpio": limpio,
        "fila_cruda_ida": (crudo_i or "")[:200],
        "error": None,
    }


def volcar(r):
    SALIDA.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    cfg = json.loads(RUTAS.read_text(encoding="utf-8"))
    DIAG.mkdir(exist_ok=True)
    destinos = cfg["destinos"][:LIMITE] if LIMITE else cfg["destinos"]
    if LIMITE:
        print(f"LIMITE={LIMITE}: solo {len(destinos)} destinos", flush=True)

    res = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "alerta": "Barrido en curso, sin terminar.",
           "unidad": "precio 'desde' por persona (ida + vuelta). La referencia "
                     "de la tabla es total de 4, por eso se compara contra "
                     "referencia/4.",
           "ventanas": {}}
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
            sin_trenes_seguidos = 0
            ventana_muerta = False

            for destino in destinos:
                if rendido or ventana_muerta:
                    motivo = ("barrido abortado antes" if rendido
                              else "ventana sin trenes: aun no esta a la venta")
                    filas.append({"destino": destino["nombre"], "precio_por_persona": None,
                                  "error": f"no intentado: {motivo}"})
                    continue

                total += 1
                try:
                    fila = barrer_ruta(page, cfg, ventana, destino)
                    filas.append(fila)
                    ok += 1
                    seguidos = 0
                    sin_trenes_seguidos = 0
                    if not fila["pasajeros_aplicados"]:
                        sin_pax += 1
                    v = fila["variacion_pct"]
                    comp = (f"ref/persona {fila['referencia_por_persona']} € | "
                            f"{'+' if v > 0 else ''}{v} %") if v is not None \
                        else "sin comparar (pasajeros no fiables)"
                    print(f"[ok] {ventana['id']}-{destino['nombre']}: "
                          f"{fila['precio_por_persona']} €/persona "
                          f"(ida {fila['ida']['precio_persona']} + "
                          f"vuelta {fila['vuelta']['precio_persona']}) | {comp}",
                          flush=True)
                    print(f"      pax: {fila['pasajeros_leidos']}", flush=True)

                except (FueraDeVenta, SinTrenes) as e:
                    fuera += 1
                    seguidos = 0
                    sin_trenes_seguidos += 1
                    filas.append({"destino": destino["nombre"],
                                  "referencia_total_4": destino.get("referencia"),
                                  "precio_por_persona": None, "error": str(e)})
                    print(f"[fuera de venta] {ventana['id']}-{destino['nombre']}: {e}",
                          flush=True)
                    if sin_trenes_seguidos >= SIN_TRENES_PARA_SALTAR_VENTANA:
                        ventana_muerta = True
                        print(f"      -> ventana '{ventana['id']}' aun no esta a la "
                              f"venta. Me la salto entera.", flush=True)

                except Exception as e:
                    fallos += 1
                    seguidos += 1
                    etq = f"{ventana['id']}-{destino['nombre']}".replace(" ", "_")
                    print(f"[fallo {seguidos}/{FALLOS_SEGUIDOS_MAX}] {etq}: {e}",
                          flush=True)
                    if diags < MAX_DIAGNOSTICOS:
                        diags += 1
                        try:
                            act = [p for p in ctx.pages if not p.is_closed()][-1]
                            print(f"      url: {act.url[:140]}", flush=True)
                            vistas = act.evaluate(JS_FILAS) or []
                            print(f"      filas con precio y hora: {len(vistas)}",
                                  flush=True)
                            for v in vistas[:10]:
                                print(f"        · {v[:170]}", flush=True)
                            if not vistas:
                                for s in (act.evaluate(JS_INPUTS) or [])[:20]:
                                    print(f"        · {s}", flush=True)
                            act.screenshot(path=str(DIAG / f"{etq}.png"))
                            (DIAG / f"{etq}.html").write_text(
                                act.content()[:400_000], encoding="utf-8")
                        except Exception as e2:
                            print(f"      (no se pudo volcar: {str(e2)[:120]})",
                                  flush=True)
                    filas.append({"destino": destino["nombre"],
                                  "referencia_total_4": destino.get("referencia"),
                                  "precio_por_persona": None, "error": str(e)[:300]})
                    if seguidos >= FALLOS_SEGUIDOS_MAX:
                        rendido = True
                        print(f"\n{FALLOS_SEGUIDOS_MAX} fallos seguidos. Abortando.",
                              flush=True)

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
                         "fiables. Mirar pasajeros_leidos.")
    elif fallos:
        res["alerta"] = f"{fallos} de {total} rutas han fallado."
    else:
        res["alerta"] = None

    res["resumen"] = {"con_precio": ok, "fallidas": fallos, "fuera_de_venta": fuera,
                      "intentadas": total, "pasajeros_mal": sin_pax}
    res["generado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    volcar(res)
    print(f"\nHecho: {ok} con precio, {fuera} fuera de venta, {fallos} fallidas.",
          flush=True)
    print(f"Alerta: {res['alerta']}", flush=True)
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
