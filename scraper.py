#!/usr/bin/env python3
"""
Radar de trenes: barre precios de Renfe y escribe precios-trenes.json.

Version 16.

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
  v9  Espera de hidratacion + reintentos. NO CAMBIO NADA: dos barridos byte a
      byte iguales. Al ser determinista, no era una carrera.
  v10 CAUSA RAIZ, visible comparando v7 con v9: Renfe RECUERDA los pasajeros
      entre busquedas, asi que cada ruta partia de un estado distinto. En la v7,
      noviembre partia de cero y sumar a ciegas FUNCIONO (2+2); diciembre partia
      del 2+2 heredado y derivo a 3+4. Las v8 y v9 intentaron adivinar ese estado
      con un delta y rompieron el caso que ya iba bien.
      Arreglo: borrar cookies y almacenamiento ANTES DE CADA BUSQUEDA. Partiendo
      siempre de "1 adulto, 0 ninos", sumar lo que falta es determinista.
      Moraleja: el problema no era como contar, era no controlar el punto de
      partida. FUNCIONO A MEDIAS: el punto de partida ya es siempre "1 adulto"
      (noviembre dejo de derivar a 3+4), pero en las tres primeras rutas el
      panel de pasajeros directamente NO SE ABRE y se quedan en 1 adulto.
  v11 NO ARREGLA NADA A CIEGAS. Tres versiones seguidas con logicas de conteo
      distintas han dado el mismo resultado, asi que el conteo no es la
      variable: el panel no llega a abrirse. Esta version instrumenta ese
      momento exacto (sonda_pax) y responde con elementFromPoint la unica
      pregunta que queda: si hay algo TAPANDO #passengersSelection. Ademas
      sube a 3 intentos con Escape + scroll arriba entre ellos, que es higiene,
      no una hipotesis. LA SONDA LO CANTO A LA PRIMERA: rect [0,0,0,0].
  v12 En la portada hay DOS #passengersSelection. El primero del DOM es el del
      buscador PLEGADO de la cabecera (rf-header-topbar-search-integration),
      mide 0x0 y siempre pone "1 adulto"; el bueno es el del buscador grande.
      Al pedir .first se cogia el fantasma y el clic se perdia. Arreglo:
      "#passengersSelection:visible", que es funcion (tiene tamano) y no nombre.
      A MEDIAS: el log del 7 sep dijo "no existe #passengersSelection:visible" en
      las 44 rutas. O sea que en ese instante NINGUNO de los dos es visible: el
      buscador grande todavia no esta pintado. El acierto seguia viniendo del
      reintento. Barrido completo igualmente: 39 precios en 46 min.
  v13 Esperar a que el selector visible exista (wait_for_selector state=visible).
      FALLO, Y ENCIMA HIZO DAÑO: el log del 7 sep repite "no existe
      #passengersSelection:visible" en las 44 rutas, o sea que el elemento NUNCA
      llega a ser visible en ese punto y la espera agotaba sus 15 s cada vez. El
      barrido paso de 46 a 53 minutos sin arreglar nada. Tambien cambie rutas.json
      a "Castellón de la Plana" creyendo que era la tilde: TAMBIEN FALSO, sigue
      fallando igual.
  v14 Deshacer la espera de la v13 y MEDIR de una vez cuantos #passengersSelection
      hay. RESULTADO: cuantos = 1, cajas = ["BUTTON[0x0] val='1 adulto'"].
      SOLO HAY UNO. Mi historia de "hay dos, el de la cabecera es un fantasma" era
      FALSA, y encima sostuvo las versiones 12 y 13. Tres versiones sobre un
      elemento inventado.
  v15 Con el dato encima, la explicacion real es aburrida: hay UN boton que mide
      0x0 mientras el calendario sigue abierto y la pagina esta scrolleada, y pasa
      a medir de verdad despues del Escape + scrollTo(0,0). Por eso el intento 1
      fallaba SIEMPRE y el intento 2 acertaba SIEMPRE: la diferencia no era esperar
      mas, era esa higiene. Arreglo: hacerla tambien en el primer intento. No es
      una hipotesis nueva, es mover al primer sitio el paso que ya funcionaba en el
      segundo. Quita ~6 s por busqueda y todo el ruido de sondas.
  v16 FILTRO DE HORA DE SALIDA (aprobado por Jorge el 8 sep). analizar() ya no
      coge el tren mas barato del dia: coge el mas barato QUE CUMPLA EL HORARIO.
      Con eso, comparar contra la referencia vuelve a significar algo. Si ninguno
      cumple NO se pierde la lectura: se devuelve el mas barato del dia con
      cumple_horario=False, variacion_pct=None y un aviso que dice por que.
      rutas.json gana "ida_salida_minima": "17:00", y cada ventana puede pisarlo:
      febrero de 2027 lo baja a 06:00 porque el 12 NO es lectivo.
      Probado offline con cinco casos antes de gastarte 46 minutos de barrido.
      YA NO PENDIENTE (era esto): "limpio" no exige hora minima de salida en
      la ida, asi que coge el tren mas barato del dia (Valladolid, viernes a las
      14:23) y lo compara contra una referencia hecha con salidas de despues de
      las 17:00. Ese -55,8 % NO es un chollo, es otro producto.
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
MAX_CAPTURAS_PAX = 6
CAPTURAS_PAX = 0
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


# En la portada hay DOS #passengersSelection: el del buscador plegado de la
# cabecera (mide 0x0 y siempre pone "1 adulto") y el del buscador grande, que es
# el bueno. ":visible" se queda con el que tiene tamano real. Medido el 6 sep:
# el primero del DOM es el fantasma, y por eso los clics se perdian.
PAX_SEL = "#passengersSelection:visible"


def resumen_pasajeros(page):
    for sel in (PAX_SEL, "#passengersSelection"):
        try:
            loc = page.locator(sel).first
            if loc.count():
                return (loc.get_attribute("value") or "").strip()
        except Exception:
            continue
    return ""


def contar_pasajeros(texto):
    """De '2 adultos, 2 niños' saca (2, 2). Por defecto Renfe arranca en 1 adulto."""
    t = (texto or "").lower()
    a = re.search(r"(\d+)\s*adulto", t)
    n = re.search(r"(\d+)\s*ni[ñn]o", t)
    return (int(a.group(1)) if a else 1, int(n.group(1)) if n else 0)


def reset_sesion(page, ctx):
    """
    Borra cookies y almacenamiento para que Renfe vuelva a su estado por
    defecto: 1 adulto, 0 ninos.

    ESTA ES LA CLAVE DE TODO EL LIO DE LOS PASAJEROS. Renfe los recuerda entre
    busquedas, asi que cada ruta arrancaba desde un estado distinto e
    impredecible (a veces 1 adulto, a veces el 2+2 de la anterior). Las v8 y v9
    intentaron adivinar ese estado con un delta; la v7, que sumaba a ciegas,
    acertaba solo cuando partia de cero.

    Partiendo siempre de cero, sumar a ciegas es determinista y correcto.
    """
    try:
        ctx.clear_cookies()
    except Exception:
        pass
    try:
        page.goto(RENFE, wait_until="domcontentloaded", timeout=45_000)
        page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); }"
                      " catch (e) {} }")
    except Exception:
        pass


JS_SONDA_PAX = r"""
() => {
  const desc = e => e ? (e.tagName.toLowerCase()
      + (e.id ? '#' + e.id : '')
      + (e.className && typeof e.className === 'string' && e.className.trim()
         ? '.' + e.className.trim().split(/\s+/).slice(0, 3).join('.') : '')) : 'nada';
  const out = {};
  // CUANTOS HAY, que es lo que nunca medi. Toda mi teoria de "hay dos
  // #passengersSelection" se apoyaba en suponerlo. Esta linea la confirma o
  // la tira abajo, y con ella la caja de cada uno.
  const todos = document.querySelectorAll('#passengersSelection');
  out.cuantos = todos.length;
  out.cajas = Array.from(todos).map(e => {
    const r = e.getBoundingClientRect();
    return `${e.tagName}[${Math.round(r.width)}x${Math.round(r.height)}] val='${e.value || ''}'`;
  });
  const inp = document.querySelector('#passengersSelection');
  out.existe = !!inp;
  if (inp) {
    out.value = inp.value;
    const r = inp.getBoundingClientRect();
    out.rect = [Math.round(r.x), Math.round(r.y),
                Math.round(r.width), Math.round(r.height)];
    out.dentro_de_pantalla = (r.top >= 0 && r.bottom <= innerHeight);
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    const top = document.elementFromPoint(cx, cy);
    out.encima = desc(top);
    out.encima_padres = [];
    let p = top && top.parentElement;
    for (let i = 0; i < 3 && p; i++) { out.encima_padres.push(desc(p)); p = p.parentElement; }
    // La pregunta que de verdad importa: quien recibe el clic, el selector o
    // algo que lo tapa.
    out.el_clic_llega = !!(top && (top === inp || inp.contains(top) || top.contains(inp)));
  }
  const lp = document.querySelector('.lightpick');
  out.lightpick = lp ? getComputedStyle(lp).display : 'no existe';
  out.botones_adulto = document.querySelectorAll("[aria-label='Añadir adulto']").length;
  const ot = document.querySelector('#onetrust-banner-sdk, .onetrust-pc-dark-filter');
  out.banner_cookies = ot ? getComputedStyle(ot).display : 'no existe';
  return out;
}
"""


def sonda_pax(page, etiqueta):
    """
    Fotografia del estado en el momento exacto en que fallan los pasajeros.
    No arregla nada: contesta la unica pregunta abierta, que es por que el panel
    se abre en unas rutas y en otras no. La respuesta util no es un nombre de
    clase, es elementFromPoint: dice si hay algo TAPANDO el selector.
    """
    lineas = [f"      [sonda pax] {etiqueta}"]
    try:
        for k, v in (page.evaluate(JS_SONDA_PAX) or {}).items():
            lineas.append(f"        {k} = {v}")
    except Exception as e:
        lineas.append(f"        la sonda revento: {str(e)[:140]}")
    print("\n".join(lineas), flush=True)
    # Capturas con tope: el barrido del 6 sep subio 24 pantallazos y 23 MB de
    # artefacto. Con seis se diagnostica igual de bien.
    global CAPTURAS_PAX
    if CAPTURAS_PAX >= MAX_CAPTURAS_PAX:
        return
    CAPTURAS_PAX += 1
    try:
        DIAG.mkdir(exist_ok=True)
        page.screenshot(path=str(DIAG / f"pax-{etiqueta}.png"))
    except Exception:
        pass


def poner_pasajeros(page, pax, etiqueta="ruta"):
    """
    Pone 2 adultos + 2 ninos partiendo SIEMPRE de 1 adulto, 0 ninos, que es lo
    que garantiza reset_sesion(). Sin delta ni adivinanzas: se suma lo que falta
    desde un punto de partida conocido, y se verifica.
    """
    objetivo = (pax["adultos"], pax["ninos"])

    for intento in range(3):
        partida = contar_pasajeros(resumen_pasajeros(page))
        if partida == objetivo:
            return True, resumen_pasajeros(page)

        # ESTO VA EN TODOS LOS INTENTOS, INCLUIDO EL PRIMERO. Medido, no supuesto:
        # solo hay UN #passengersSelection y mide 0x0 mientras el calendario sigue
        # abierto y la pagina esta scrolleada. El intento 1 fallaba el 100 % de las
        # veces sin esto y el intento 2 acertaba el 100 % con esto. No es una
        # hipotesis nueva: es mover al primer sitio el paso que ya funcionaba en el
        # segundo.
        try:
            page.keyboard.press("Escape")
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
        except Exception:
            pass

        modo = None
        try:
            modo = click_robusto(page, PAX_SEL, timeout=6000)
            page.wait_for_selector("[aria-label='Añadir adulto']", timeout=8000)
            page.wait_for_timeout(500)
        except Exception as e:
            print(f"      [pax] no se abrio el panel en el intento {intento + 1} "
                  f"(modo de clic: {modo}): {str(e)[:120]}", flush=True)
            sonda_pax(page, f"{etiqueta}-i{intento + 1}")
            page.wait_for_timeout(800)
            continue

        def pulsar(sel, veces):
            for _ in range(max(0, veces)):
                try:
                    loc = page.locator(sel).first
                    if not (loc.count() and loc.is_visible()):
                        return
                    loc.click(timeout=3000)
                    page.wait_for_timeout(400)
                except Exception:
                    return

        a_hay, n_hay = partida
        pulsar("[aria-label='Añadir adulto']", objetivo[0] - a_hay)
        pulsar("[aria-label='Añadir niño mayor de 4']", objetivo[1] - n_hay)

        try:
            page.click("button:has-text('Listo')", timeout=4000)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        page.wait_for_timeout(700)

        if contar_pasajeros(resumen_pasajeros(page)) == objetivo:
            return True, resumen_pasajeros(page)

    final = resumen_pasajeros(page)
    ok = contar_pasajeros(final) == objetivo
    if not ok:
        sonda_pax(page, f"{etiqueta}-final")
    return ok, final


def buscar_un_sentido(page, origen, destino, fecha, pax, ctx=None):
    # Estado limpio SIEMPRE: sin esto Renfe hereda los pasajeros de la busqueda
    # anterior y el punto de partida deja de ser conocido.
    if ctx is not None:
        reset_sesion(page, ctx)
    page.goto(RENFE, wait_until="domcontentloaded", timeout=45_000)
    aceptar_cookies(page)
    page.wait_for_timeout(800)

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
    etiqueta = re.sub(r"[^A-Za-z0-9]+", "-", f"{destino}-{fecha}").strip("-").lower()
    pax_ok, pax_txt = poner_pasajeros(page, pax, etiqueta)
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


def analizar(filas, etiqueta, salida_min=None, llegada_max=None):
    """
    Elige el tren mas barato QUE CUMPLA EL HORARIO, no el mas barato del dia.

    Esta es la diferencia entre comparar y mentir. La tabla de referencia esta
    hecha con salidas de despues de las 17:00 el viernes; hasta ahora el scraper
    cogia el tren de las 14:23 y lo comparaba contra ella, lo que daba un
    Valladolid a -55,8 % que no era una bajada de precio sino otro producto.

    Si ningun tren cumple el horario NO se pierde la lectura: se devuelve el mas
    barato del dia con cumple=False, y quien llame decide no compararlo.
    """
    candidatos, horas = [], []
    for txt in filas:
        precio = eur(txt)
        todas = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", txt)
        salida = todas[0] if todas else None
        llegada = todas[1] if len(todas) > 1 else None
        tren = next((t for t in ("AVE", "AVLO", "AVANT", "ALVIA", "MD", "INTERCITY")
                     if t in txt.upper()), None)
        if salida:
            horas.append(salida)
        if precio is None:
            continue
        cumple = ((salida_min is None or despues(salida, salida_min))
                  and (llegada_max is None or antes(llegada, llegada_max)))
        candidatos.append({"precio": precio, "salida": salida, "llegada": llegada,
                           "tren": tren, "cumple": cumple, "crudo": txt})

    if not candidatos:
        raise RuntimeError(f"filas sin precio legible en {etiqueta}")

    barato_global = min(candidatos, key=lambda c: c["precio"])
    limpios = [c for c in candidatos if c["cumple"]]
    elegido = min(limpios, key=lambda c: c["precio"]) if limpios else barato_global

    return {
        "precio": elegido["precio"],
        "salida": elegido["salida"],
        "llegada": elegido["llegada"],
        "tren": elegido["tren"],
        "cumple_horario": bool(limpios),
        # Lo que costaba antes de filtrar, solo como informacion. NO se compara
        # con la referencia: es de otro horario.
        "precio_mas_barato_del_dia": barato_global["precio"],
        "salida_mas_barato_del_dia": barato_global["salida"],
        "trenes_leidos": len(candidatos),
        "trenes_en_horario": len(limpios),
        "mas_temprano": min(horas) if horas else None,
        "mas_tardio": max(horas) if horas else None,
        "crudo": elegido["crudo"],
    }


def barrer_ruta(page, cfg, ventana, destino, ctx=None):
    origen = cfg["origen"]["nombre"]
    pax = cfg["pasajeros"]
    n_pax = pax["adultos"] + pax["ninos"]

    # El horario base vive en cfg, pero cada ventana puede pisarlo. Hace falta:
    # el 12 de febrero de 2027 NO es lectivo, asi que ahi si se puede salir por
    # la manana y exigir las 17:00 tiraria las lecturas buenas.
    hl = {**cfg["horario_limpio"], **(ventana.get("horario_limpio") or {})}

    f_i, ok1, pax1, met1 = buscar_un_sentido(page, origen, destino["nombre"],
                                             ventana["salida"], pax, ctx)
    ida = analizar(f_i, "ida",
                   hl.get("ida_salida_minima"), hl.get("ida_llegada_maxima"))

    f_v, ok2, pax2, met2 = buscar_un_sentido(page, destino["nombre"], origen,
                                             ventana["vuelta"], pax, ctx)
    vuelta = analizar(f_v, "vuelta",
                      hl.get("vuelta_salida_minima"), hl.get("vuelta_llegada_maxima"))

    p_i, s_i, l_i, t_i, crudo_i = (ida["precio"], ida["salida"], ida["llegada"],
                                   ida["tren"], ida["crudo"])
    p_v, s_v, l_v, t_v = (vuelta["precio"], vuelta["salida"], vuelta["llegada"],
                          vuelta["tren"])
    temp_i, tard_v = ida["mas_temprano"], vuelta["mas_tardio"]

    pax_ok = ok1 and ok2
    limpio = ida["cumple_horario"] and vuelta["cumple_horario"]

    # UNIDADES. Lo medido es "desde" POR PERSONA. La referencia es total de 4.
    # Se compara por persona contra referencia/4: mismas unidades, sin inventar.
    por_persona = round(p_i + p_v, 2)
    ref_total = destino.get("referencia")
    ref_persona = round(ref_total / n_pax, 2) if ref_total else None
    # SOLO se compara si ademas de los pasajeros correctos el horario cuadra.
    # Un precio de otro horario no es un precio mas bajo, es otro viaje.
    var = (round((por_persona - ref_persona) / ref_persona * 100, 1)
           if (ref_persona and pax_ok and limpio) else None)

    motivos = []
    if not pax_ok:
        motivos.append(f"el buscador decia '{pax1}' y '{pax2}'")
    if not ida["cumple_horario"]:
        motivos.append(f"ninguna ida sale despues de {hl.get('ida_salida_minima')} "
                       f"(la mas barata sale a las {s_i})")
    if not vuelta["cumple_horario"]:
        motivos.append(f"ninguna vuelta cumple el horario "
                       f"(la mas barata sale a las {s_v})")

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
        "aviso": None if not motivos else
                 "NO COMPARABLE: " + "; ".join(motivos) + ".",
        "ida": {"salida": s_i, "llegada": l_i, "tren": t_i, "precio_persona": p_i,
                "en_horario": ida["cumple_horario"],
                "trenes_en_horario": ida["trenes_en_horario"],
                "trenes_leidos": ida["trenes_leidos"],
                "mas_barato_del_dia": ida["precio_mas_barato_del_dia"],
                "sale_el_mas_barato_a": ida["salida_mas_barato_del_dia"]},
        "vuelta": {"salida": s_v, "llegada": l_v, "tren": t_v, "precio_persona": p_v,
                   "en_horario": vuelta["cumple_horario"],
                   "trenes_en_horario": vuelta["trenes_en_horario"],
                   "trenes_leidos": vuelta["trenes_leidos"],
                   "mas_barato_del_dia": vuelta["precio_mas_barato_del_dia"],
                   "sale_el_mas_barato_a": vuelta["salida_mas_barato_del_dia"]},
        "mas_temprano": temp_i,
        "mas_tardio": tard_v,
        "limpio": limpio,
        "fila_cruda_ida": (crudo_i or "")[:200],
        "error": None,
    }


def volcar(r):
    SALIDA.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    # Cartel de version: si el log no empieza por esta linea, el fichero que se
    # esta ejecutando NO es este scraper (paso el 6 sep 2026: scraper.py del repo
    # tenia dentro el codigo de la sonda y el barrido nunca corrio).
    print("=== RADAR DE TRENES scraper.py v16 ===", flush=True)
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
                    fila = barrer_ruta(page, cfg, ventana, destino, ctx)
                    filas.append(fila)
                    ok += 1
                    seguidos = 0
                    sin_trenes_seguidos = 0
                    if not fila["pasajeros_aplicados"]:
                        sin_pax += 1
                    v = fila["variacion_pct"]
                    comp = (f"ref/persona {fila['referencia_por_persona']} € | "
                            f"{'+' if v > 0 else ''}{v} %") if v is not None \
                        else "SIN COMPARAR"
                    print(f"[ok] {ventana['id']}-{destino['nombre']}: "
                          f"{fila['precio_por_persona']} €/persona "
                          f"(ida {fila['ida']['precio_persona']} + "
                          f"vuelta {fila['vuelta']['precio_persona']}) | {comp}",
                          flush=True)
                    print(f"      pax: {fila['pasajeros_leidos']} | "
                          f"ida {fila['ida']['salida']} "
                          f"({fila['ida']['trenes_en_horario']}/"
                          f"{fila['ida']['trenes_leidos']} en horario) · "
                          f"vuelta {fila['vuelta']['salida']} "
                          f"({fila['vuelta']['trenes_en_horario']}/"
                          f"{fila['vuelta']['trenes_leidos']})", flush=True)
                    if fila["aviso"]:
                        print(f"      {fila['aviso']}", flush=True)

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
