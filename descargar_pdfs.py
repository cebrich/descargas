"""Descarga los documentos de resultados de estila.selae.es y los guarda en descargas/.

URL de cada documento:
  https://estila.selae.es/documents/20143/<CARPETA>/<Título+con+más>

Credenciales por variables de entorno (nunca en el código):
  S_USER, S_PASS
Opcionales:
  OUT_DIR   -> carpeta de destino (por defecto "descargas")
  HEADLESS  -> 0 abre el navegador a la vista
  NAVEGADOR -> chrome / msedge: usa el navegador instalado en el PC

Los nombres de los archivos en eSTILA no son del todo regulares ("Resultado" o
"Resultados", espacios dobles, sorteos que se mueven de día...), así que de cada
documento se prueban varias variantes del nombre.
"""
import base64
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus, urlparse

from playwright.sync_api import sync_playwright

START_URL = "https://estila.selae.es/"
LOGIN_URL = "https://gus-forms.lbext.selae.es/gus/loginSELAE"
BASE_URL = "https://estila.selae.es/documents/20143"
OUT_DIR = Path(os.environ.get("OUT_DIR", "descargas"))
ESTADO = OUT_DIR / "estado.json"
PAUSA_S = 0.15  # pausa entre peticiones, para no saturar la web
INTENTOS_LOGIN = 3

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

EXTENSIONES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

# --- Documentos con uno por sorteo y día --------------------------------------------
# En la plantilla: {s} = semana, {r} = "Resultado"/"Resultados", {d} = día.
# "moverse": si el sorteo puede celebrarse otro día (se prueban los dos siguientes).
# "sufijo_dia": si el nombre del archivo guardado lleva el día.
SORTEOS = [
    {"nombre": "BonoLoto", "carpeta": "51612786", "plantilla": "S {s} {r} BonoLoto {d}",
     "dias": DIAS, "moverse": False},
    {"nombre": "EuroDreams", "carpeta": "562184884", "plantilla": "S {s} {r} EuroDreams {d}",
     "dias": ["Lunes", "Jueves"]},
    {"nombre": "EuroMillones", "carpeta": "27931469", "plantilla": "S {s} {r} EuroMillones {d}",
     "dias": ["Martes", "Viernes"]},
    {"nombre": "LaPrimitiva", "carpeta": "27931445", "plantilla": "S {s} {r} La Primitiva {d}",
     "dias": ["Lunes", "Jueves", "Sábado"]},
    {"nombre": "ElGordo", "carpeta": "27931457",
     "plantilla": "S {s} {r} El Gordo de La Primitiva {d}", "dias": ["Domingo"]},
    {"nombre": "ListaPremios", "carpeta": "27964009", "plantilla": "S {s} Lista Premios {d}",
     "dias": ["Sábado"]},
    {"nombre": "ListaPremios", "carpeta": "168541635", "plantilla": "S {s} Lista Premios {d}",
     "dias": ["Jueves"]},
    {"nombre": "VisorBotes", "carpeta": "545955054", "plantilla": "S {s} Visor Botes",
     "dias": ["Domingo"], "moverse": False, "sufijo_dia": False},
]

# --- Documentos por jornada (sin día en el nombre) ----------------------------------
# "inicio": primera jornada a descargar si aún no hay ninguna guardada.
JORNADAS = [
    {"nombre": "QuiniGol", "carpeta": "27931463",
     "plantilla": "S {s} {r} QuiniGol Jornada {j}", "inicio": 22},
    {"nombre": "LaQuiniela", "carpeta": "27931439",
     "plantilla": "S {s} {r} La Quiniela Jornada {j}", "inicio": 21},
    {"nombre": "LotoTurf", "carpeta": "27931541",
     "plantilla": "S {s} {r} LotoTurf Jornada {j}", "inicio": 71},
    {"nombre": "QuintuplePlus", "carpeta": "27931547",
     "plantilla": "S {s} {r} QuíntuplePlus Jornada {j}", "inicio": 71},
]

# --- Clasificaciones: solo interesa la última; se guarda siempre con el mismo nombre ---
# Su enlace real lleva un código aleatorio al final, imposible de adivinar, así que se
# buscan en el "Servicio de Descarga" de eSTILA y se toma el enlace de la página.
# "patron": nombre del archivo; los números capturados (semana, jornadas) ordenan los
# resultados para quedarse con el más reciente.
LIGAS = [
    {"nombre": "LaLiga_Clasificaciones", "carpeta": "27931523",
     "busquedas": ["Clasificaciones", "Clasificaciones 1a y 2a Div"],
     "patron": r"S-(\d+) Clasificaciones 1a y 2a Div Jda (\d+) y (\d+)\.pdf"},
    {"nombre": "LigaF_Clasificaciones", "carpeta": "678472319",
     "busquedas": ["CLASI_LigaF", "LigaF", "CLASI", "Liga F", "Clasificaciones"],
     "patron": r"S_(\d+)_CLASI_LigaF_J(\d+)\.pdf"},
]


def diagnostico(page, motivo: str, nombre: str = "error_login") -> None:
    """Guarda captura y HTML para ver qué cargó realmente en el runner."""
    print(f"DIAGNÓSTICO: {motivo}", file=sys.stderr)
    print(f"URL actual: {page.url}", file=sys.stderr)
    for f in page.frames:
        print(f"  frame: {f.url}", file=sys.stderr)
        try:
            for html in f.eval_on_selector_all("input, button", "els => els.map(e => e.outerHTML)"):
                print(f"    {html[:300]}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
    try:
        print(f"Título: {page.title()!r}", file=sys.stderr)
        print(f"Texto visible: {page.inner_text('body')[:500]!r}", file=sys.stderr)
        sombras = page.evaluate(
            "[...document.querySelectorAll('*')].filter(e => e.shadowRoot).length"
        )
        print(f"Elementos con shadow DOM: {sombras}", file=sys.stderr)
        print(f"HTML (inicio): {page.content()[:1500]!r}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo leer la página: {e}", file=sys.stderr)
    try:
        page.screenshot(path=f"{nombre}.png", full_page=True)
        Path(f"{nombre}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo guardar el diagnóstico: {e}", file=sys.stderr)


def buscar_formulario(page, espera_s: int = 40):
    """Devuelve (frame, campo_usuario, campo_clave) buscando en la página y en iframes."""
    limite = time.time() + espera_s
    while time.time() < limite:
        for frame in page.frames:
            usuario = frame.get_by_placeholder("Usuario")
            clave = frame.get_by_placeholder("Contraseña")
            if usuario.count() and clave.count():
                return frame, usuario.first, clave.first
            # Plan B: los dos primeros campos de texto visibles (usuario, contraseña)
            campos = frame.locator(
                "input:visible:not([type='hidden']):not([type='submit'])"
                ":not([type='button']):not([type='checkbox'])"
            )
            if campos.count() >= 2:
                return frame, campos.nth(0), campos.nth(1)
        page.wait_for_timeout(500)
    return None


def login(page, user: str, password: str):
    """Devuelve True si entra, False si falla, o "robot" si la web pide limpiar cookies."""
    # El login solo funciona si se llega desde eSTILA (si no, sale "Redirección
    # incorrecta"), así que se empieza en estila.selae.es y se pulsa "Acceder".
    resp = page.goto(START_URL, wait_until="domcontentloaded", timeout=60_000)
    print(f"Página de inicio: HTTP {resp.status if resp else '?'} -> {page.url}", file=sys.stderr)
    page.wait_for_timeout(2_000)
    # Puede haber varios textos "Acceder" (p. ej. uno oculto del menú móvil):
    # se pulsa el primero que sea visible.
    acceder = page.get_by_text("Acceder", exact=True)
    pulsado = False
    limite = time.time() + 20
    while time.time() < limite and not pulsado:
        n = acceder.count()
        for i in range(n):
            if acceder.nth(i).is_visible():
                print(f"'Acceder': {n} coincidencia(s); se pulsa la nº {i + 1}.", file=sys.stderr)
                acceder.nth(i).click()
                pulsado = True
                break
        if not pulsado:
            page.wait_for_timeout(500)
    if pulsado:
        try:
            page.wait_for_url(lambda u: "gus-forms" in u, timeout=30_000)
        except Exception:  # noqa: BLE001
            print("Tras pulsar 'Acceder' no se llegó a la página de login.", file=sys.stderr)
        print(f"URL tras 'Acceder': {page.url}", file=sys.stderr)
    else:
        diagnostico(page, "no se encontró un botón 'Acceder' visible en la página de inicio", "error_inicio")
        print("Se prueba la URL de login directa.", file=sys.stderr)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    form = buscar_formulario(page)
    if form is None:
        diagnostico(page, "no aparece el formulario de login (usuario + contraseña)")
        return False
    frame, campo_usuario, campo_clave = form
    texto = page.inner_text("body")
    if "Redirección incorrecta" in texto:
        diagnostico(page, "el login muestra 'Redirección incorrecta' (no se llegó desde eSTILA)")
        return False
    if "No podemos determinar" in texto:
        return "robot"  # la web pide limpiar cookies y reintentar
    # Se escribe tecla a tecla: el formulario suele activar "Enviar" al detectar
    # pulsaciones, cosa que fill() no provoca.
    campo_usuario.click()
    campo_usuario.press_sequentially(user, delay=60)
    campo_clave.click()
    campo_clave.press_sequentially(password, delay=60)
    boton = frame.locator(
        "button[type='submit'], input[type='submit'], button:has-text('Enviar')"
    )
    enviado = False
    if boton.count():
        try:
            # no_wait_after: el envío provoca varias redirecciones y no hay que esperarlas aquí
            boton.first.click(timeout=10_000, no_wait_after=True)
            enviado = True
        except Exception as e:  # noqa: BLE001
            print(f"No se pudo pulsar 'Enviar' ({str(e).splitlines()[0]}); se prueba con Enter.", file=sys.stderr)
    if not enviado and "gus-forms" in page.url:  # si ya salió del login, el envío fue bueno
        campo_clave.press("Enter", no_wait_after=True)
    # esperar a salir del formulario de login (redirecciones hacia eSTILA)
    try:
        page.wait_for_url(lambda u: "gus-forms" not in u, timeout=60_000)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.wait_for_load_state("load", timeout=20_000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1_500)
    try:
        if "No podemos determinar" in page.inner_text("body"):
            return "robot"
    except Exception:  # noqa: BLE001
        pass
    return True


# --- Nombres, fechas y descargas ------------------------------------------------------

def url_documento(carpeta: str, titulo: str) -> str:
    return f"{BASE_URL}/{carpeta}/{quote_plus(titulo)}"


def variantes(plantilla: str, **datos):
    """Variantes razonables del título: Resultado/Resultados y espacio simple o doble
    antes de la última palabra (día o número de jornada)."""
    palabras = ["Resultado", "Resultados"] if "{r}" in plantilla else [""]
    for r in palabras:
        titulo = plantilla.format(r=r, **datos)
        yield titulo
        antes, _, ultimo = titulo.rpartition(" ")
        yield f"{antes}  {ultimo}"


def fechas_candidatas(sorteo: dict, dia: str, fecha: date, hoy: date) -> list[date]:
    """Fechas en las que puede estar publicado el sorteo de `dia` (cuya fecha habitual
    es `fecha`): la habitual y, si puede moverse, los dos días siguientes que no sean
    otro día habitual del mismo sorteo. Nunca fechas futuras."""
    desplazamientos = (0, 1, 2) if sorteo.get("moverse", True) else (0,)
    resultado = []
    for k in desplazamientos:
        f = fecha + timedelta(days=k)
        if f > hoy:
            break
        if k and DIAS[f.weekday()] in sorteo["dias"]:
            continue
        resultado.append(f)
    return resultado


def intentar(context, carpeta: str, titulos):
    """Prueba cada título; devuelve (contenido, tipo) del primero que exista."""
    for titulo in titulos:
        try:
            resp = context.request.get(url_documento(carpeta, titulo))
            tipo = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.ok and tipo and tipo != "text/html":
                return resp.body(), tipo
        except Exception as e:  # noqa: BLE001
            print(f"Error de red con '{titulo}': {str(e).splitlines()[0]}", file=sys.stderr)
        time.sleep(PAUSA_S)
    return None


# Descarga desde dentro de la propia página de eSTILA (con las cabeceras de un
# navegador normal), por si la web rechaza las peticiones directas a archivos .pdf.
JS_DESCARGA = """async (url) => {
  const r = await fetch(url, {credentials: 'include'});
  const tipo = (r.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  if (!r.ok) return {estado: r.status, tipo};
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = '';
  for (let i = 0; i < buf.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  }
  return {estado: r.status, tipo, datos: btoa(bin)};
}"""


def bajar_en_pagina(page, url: str):
    """Descarga `url` desde dentro de la página de eSTILA; devuelve (contenido, tipo)."""
    try:
        r = page.evaluate(JS_DESCARGA, url)
        if r["estado"] == 200 and r["tipo"] and r["tipo"] != "text/html" and r.get("datos"):
            return base64.b64decode(r["datos"]), r["tipo"]
        print(f"  sin éxito: HTTP {r['estado']} ({r['tipo'] or '?'}) {url}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"Error de red con {url}: {str(e).splitlines()[0]}", file=sys.stderr)
    return None


def guardar(base: str, cuerpo: bytes, tipo: str) -> None:
    destino = OUT_DIR / f"{base}{EXTENSIONES.get(tipo, '.bin')}"
    destino.write_bytes(cuerpo)
    print(f"Descargado: {destino.name}")


def existe(base: str) -> bool:
    return any(OUT_DIR.glob(f"{base}.*"))


def procesar_sorteos(context, hoy: date):
    nuevos, pendientes = 0, []
    for sorteo in SORTEOS:
        for dia in sorteo["dias"]:
            for referencia in (hoy, hoy - timedelta(days=7)):
                anio, sem, _ = referencia.isocalendar()
                fecha = date.fromisocalendar(anio, sem, DIAS.index(dia) + 1)
                if fecha > hoy:
                    continue
                base = f"S{sem}_{sorteo['nombre']}"
                if sorteo.get("sufijo_dia", True):
                    base += f"_{dia}"
                if existe(base):
                    continue
                resultado = None
                for f in fechas_candidatas(sorteo, dia, fecha, hoy):
                    resultado = intentar(
                        context, sorteo["carpeta"],
                        variantes(sorteo["plantilla"], s=f.isocalendar()[1], d=DIAS[f.weekday()]),
                    )
                    if resultado:
                        break
                if resultado:
                    guardar(base, *resultado)
                    nuevos += 1
                else:
                    pendientes.append(base)
    return nuevos, pendientes


def procesar_jornadas(context, hoy: date):
    nuevos = 0
    for j in JORNADAS:
        guardadas = []
        for p in OUT_DIR.glob(f"S*_{j['nombre']}_Jornada*.*"):
            try:
                guardadas.append(int(p.stem.rsplit("Jornada", 1)[1]))
            except ValueError:
                pass
        numero = max(guardadas) + 1 if guardadas else j["inicio"]
        fallos = 0
        while fallos < 3:  # se mira hasta 3 jornadas por delante de la última guardada
            resultado = None
            for referencia in (hoy, hoy - timedelta(days=7)):
                sem = referencia.isocalendar()[1]
                resultado = intentar(
                    context, j["carpeta"], variantes(j["plantilla"], s=sem, j=numero)
                )
                if resultado:
                    guardar(f"S{sem}_{j['nombre']}_Jornada{numero}", *resultado)
                    nuevos += 1
                    break
            fallos = 0 if resultado else fallos + 1
            numero += 1
    return nuevos


def pulsar_texto(page, texto, espera_s: int = 20) -> bool:
    """Pulsa el primer elemento visible cuyo texto sea `texto` (cadena exacta o regex)."""
    patron = page.get_by_text(texto, exact=True) if isinstance(texto, str) else page.get_by_text(texto)
    limite = time.time() + espera_s
    while time.time() < limite:
        for i in range(patron.count()):
            if patron.nth(i).is_visible():
                patron.nth(i).click()
                return True
        page.wait_for_timeout(500)
    return False


RE_MENU = re.compile(r"^\s*men[úu]", re.I)
RE_DESCARGAS = re.compile(r"^\s*descargas\s*$", re.I)
RE_SERVICIO = re.compile(r"^\s*servicio de descarga\s*$", re.I)

# Posibles lupas que despliegan el buscador (de más a menos específica)
LUPAS = [
    ".management-bar button:has(svg.lexicon-icon-search)",
    ".management-bar [aria-label*='uscar' i]",
    "button:has(svg.lexicon-icon-search)",
    "a:has(svg.lexicon-icon-search)",
    "[aria-label*='uscar' i]",
    "[title*='uscar' i]",
]


def buscador_visible(page) -> bool:
    caja = page.get_by_placeholder("Buscar por")
    return caja.count() > 0 and caja.first.is_visible()


def mostrar_buscador(page, espera_s: int = 20) -> bool:
    """Deja visible el campo de búsqueda; si está plegado, pulsa la lupa."""
    limite = time.time() + espera_s
    while time.time() < limite:
        if buscador_visible(page):
            return True
        for selector in LUPAS:
            lupa = page.locator(selector)
            for i in range(lupa.count()):
                if lupa.nth(i).is_visible():
                    try:
                        lupa.nth(i).click(timeout=3_000)
                    except Exception:  # noqa: BLE001
                        continue
                    page.wait_for_timeout(1_000)
                    if buscador_visible(page):
                        return True
        page.wait_for_timeout(500)
    return False


def descargas_visible(page) -> bool:
    loc = page.get_by_text(RE_DESCARGAS)
    return any(loc.nth(i).is_visible() for i in range(loc.count()))


def abrir_menu(page, espera_s: int = 30) -> bool:
    """Deja visible la opción 'Descargas': prueba a hacer clic y, si no, a pasar el ratón
    por encima del MENÚ (según el caso se despliega de una forma u otra)."""
    limite = time.time() + espera_s
    while time.time() < limite:
        if descargas_visible(page):
            return True
        candidatos = [
            page.get_by_role("button", name=RE_MENU),
            page.get_by_role("link", name=RE_MENU),
            page.get_by_text(RE_MENU),
            page.locator("[aria-label*='men' i], [title*='men' i], .navbar-toggler, .dropdown-toggle"),
        ]
        for loc in candidatos:
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                for accion in ("clic", "hover"):
                    try:
                        el.click(timeout=3_000, no_wait_after=True) if accion == "clic" else el.hover(timeout=3_000)
                    except Exception:  # noqa: BLE001
                        continue
                    page.wait_for_timeout(1_000)
                    if descargas_visible(page):
                        return True
        page.wait_for_timeout(1_000)
    return False


def esperar_red(page, espera_s: int = 4) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=espera_s * 1000)
    except Exception:  # noqa: BLE001
        pass


def abrir_servicio_descarga(page) -> bool:
    """MENÚ > Descargas > Servicio de Descarga > lupa, como hace una persona en eSTILA."""
    if urlparse(page.url).hostname != "estila.selae.es":
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60_000)
    esperar_red(page)
    pulsar_texto(page, "Aceptar Cookies", 3)  # el aviso de cookies tapa parte de la página
    if not abrir_menu(page) or not pulsar_texto(page, RE_DESCARGAS, 5):
        return False
    esperar_red(page)
    if not pulsar_texto(page, RE_SERVICIO, 20):
        return False
    esperar_red(page)
    return mostrar_buscador(page)


def enlaces_de_busqueda(page, termino: str) -> list[str]:
    """Busca `termino` en el Servicio de Descarga y devuelve los enlaces a documentos."""
    if not mostrar_buscador(page):
        return []
    caja = page.get_by_placeholder("Buscar por").first
    caja.fill(termino)
    caja.press("Enter")
    esperar_red(page, 5)
    page.wait_for_timeout(1_000)
    return page.eval_on_selector_all("a[href*='/documents/']", "els => els.map(e => e.href)")


def titulo_de_enlace(href: str):
    """Título del archivo en un enlace …/documents/<grupo>/<carpeta>/<título>/<uuid>."""
    partes = urlparse(href).path.split("/")
    if "documents" not in partes:
        return None
    i = partes.index("documents")
    if len(partes) <= i + 3:
        return None
    return unquote_plus(partes[i + 3])


def procesar_ligas(page, hoy: date, estado: dict):
    nuevos = 0
    try:
        listo = abrir_servicio_descarga(page)
    except Exception as e:  # noqa: BLE001
        print(f"Error al abrir el Servicio de Descarga: {str(e).splitlines()[0]}", file=sys.stderr)
        listo = False
    if not listo:
        diagnostico(page, "no se pudo abrir Descargas > Servicio de Descarga", "error_liga")
        return 0
    for liga in LIGAS:
        candidatos = {}  # clave numérica (semana, jornadas) -> enlace
        vistos = []
        for termino in liga["busquedas"]:
            enlaces = enlaces_de_busqueda(page, termino)
            if not candidatos and not any(titulo_de_enlace(h) for h in enlaces):
                print(f"  búsqueda '{termino}': {len(enlaces)} enlaces; ejemplos: {enlaces[:3]}",
                      file=sys.stderr)
            for href in enlaces:
                titulo = titulo_de_enlace(href)
                if titulo is None:
                    continue
                vistos.append(titulo)
                m = re.fullmatch(liga["patron"], titulo)
                if m:
                    candidatos[tuple(int(x) for x in m.groups())] = href
            if candidatos:
                break
        if not candidatos:
            print(f"No se encontró {liga['nombre']} en el Servicio de Descarga. "
                  f"Documentos vistos: {sorted(set(vistos))[:8]}", file=sys.stderr)
            try:
                page.screenshot(path=f"error_{liga['nombre']}.png", full_page=True)
            except Exception:  # noqa: BLE001
                pass
            continue
        clave = max(candidatos)
        href = candidatos[clave]
        destino = OUT_DIR / f"{liga['nombre']}.pdf"
        if estado.get(liga["nombre"]) == list(clave) and destino.exists():
            continue  # ya está la última
        if "download=true" not in href:
            href += ("&" if "?" in href else "?") + "download=true"
        resultado = bajar_en_pagina(page, href)
        if resultado:
            destino.write_bytes(resultado[0])  # sustituye al anterior
            estado[liga["nombre"]] = list(clave)
            nuevos += 1
            print(f"Descargado: {destino.name} (semana {clave[0]}, {clave[1:]})")
    return nuevos


def main() -> int:
    user = os.environ.get("S_USER")
    password = os.environ.get("S_PASS")
    if not user or not password:
        print("Faltan S_USER / S_PASS", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    estado = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    estado_inicial = json.dumps(estado, sort_keys=True)
    hoy = date.today()
    t_inicio = time.time()

    with sync_playwright() as p:
        # HEADLESS=0 abre el navegador a la vista (útil si la web lo bloquea en modo oculto)
        # NAVEGADOR=chrome (o msedge) usa el navegador instalado en el PC en vez del
        # Chromium "headless" de Playwright, que eSTILA rechaza con 403.
        # En Windows, por defecto: Chrome instalado y con ventana visible.
        en_windows = sys.platform == "win32"
        opciones = {"headless": os.environ.get("HEADLESS", "0" if en_windows else "1") != "0"}
        canal = os.environ.get("NAVEGADOR") or ("chrome" if en_windows else "")
        if canal:
            opciones["channel"] = canal
        print(f"Navegador: {canal or 'Chromium de Playwright'}, "
              f"{'oculto' if opciones['headless'] else 'visible'}", file=sys.stderr)
        browser = p.chromium.launch(**opciones)
        # Si la web pide "limpiar cookies y volver a intentarlo", se repite desde cero con
        # un contexto nuevo (sin cookies) tras una pausa.
        for intento in range(1, INTENTOS_LOGIN + 1):
            context = browser.new_context()
            page = context.new_page()
            try:
                resultado_login = login(page, user, password)
            except Exception as e:  # noqa: BLE001
                diagnostico(page, f"fallo durante el login: {str(e).splitlines()[0]}")
                return 2
            if resultado_login is True and "login" not in page.url.lower():
                break
            if resultado_login == "robot" or resultado_login is True:
                print(f"Intento {intento}/{INTENTOS_LOGIN}: la web pide limpiar cookies y reintentar.",
                      file=sys.stderr)
                if intento < INTENTOS_LOGIN:
                    context.close()
                    time.sleep(30 * intento)
                    continue
                diagnostico(page, "el login sigue sin entrar tras varios intentos", "error_login")
                return 3
            return 2

        t0 = time.time()
        print(f"Login correcto ({t0 - t_inicio:.0f} s). Buscando documentos...", file=sys.stderr)
        nuevos, pendientes = procesar_sorteos(context, hoy)
        print(f"Sorteos listos ({time.time() - t0:.0f} s).", file=sys.stderr)
        nuevos += procesar_jornadas(context, hoy)
        print(f"Jornadas listas ({time.time() - t0:.0f} s).", file=sys.stderr)
        nuevos += procesar_ligas(page, hoy, estado)
        browser.close()

    if json.dumps(estado, sort_keys=True) != estado_inicial:
        ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Nuevos: {nuevos} | pendientes: {len(pendientes)}")
    if pendientes:
        print("Pendientes (aún no publicados o con otro nombre): " + ", ".join(pendientes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
