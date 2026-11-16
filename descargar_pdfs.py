"""Descarga documentos semanales de estila.selae.es (BonoLoto y Visor Botes).

URL de cada documento:
  https://estila.selae.es/documents/20143/<CARPETA>/<Título+con+más>

Credenciales por variables de entorno (nunca en el código):
  S_USER, S_PASS
Opcionales:
  SEMANA   -> fuerza un número de semana (por defecto prueba la semana ISO
              de hoy y la anterior, por si algún día quedó pendiente)
  OUT_DIR  -> carpeta de destino (por defecto "descargas")
"""
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://gus-forms.lbext.selae.es/gus/loginSELAE"
BASE_URL = "https://estila.selae.es/documents/20143"
OUT_DIR = Path(os.environ.get("OUT_DIR", "descargas"))

CARPETA_BONOLOTO = "51612786"
CARPETA_VISOR = "545955054"

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# (carpeta, plantilla del título, nombre base del archivo guardado)
DOCUMENTOS = [
    (CARPETA_BONOLOTO, f"S {{s}} Resultado BonoLoto {dia}", f"BonoLoto_{dia}")
    for dia in DIAS
] + [
    (CARPETA_VISOR, "S {s} Visor Botes", "VisorBotes"),
]

EXTENSIONES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


def semanas() -> list[int]:
    if os.environ.get("SEMANA"):
        return [int(os.environ["SEMANA"])]
    actual = date.today().isocalendar()[1]
    return [actual, actual - 1]


def diagnostico(page, motivo: str) -> None:
    """Guarda captura y HTML para ver qué cargó realmente en el runner."""
    print(f"DIAGNÓSTICO: {motivo}", file=sys.stderr)
    print(f"URL actual: {page.url}", file=sys.stderr)
    for f in page.frames:
        print(f"  frame: {f.url}", file=sys.stderr)
    try:
        page.screenshot(path="error_login.png", full_page=True)
        Path("error_login.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo guardar el diagnóstico: {e}", file=sys.stderr)


def buscar_formulario(page, espera_s: int = 40):
    """Devuelve (frame, campo_usuario, campo_clave) buscando en la página y en iframes."""
    limite = time.time() + espera_s
    while time.time() < limite:
        for frame in page.frames:
            clave = frame.locator("input[type='password']:visible")
            if clave.count():
                usuario = frame.locator(
                    "input[type='text']:visible, input[type='email']:visible, "
                    "input:not([type]):visible"
                )
                if usuario.count():
                    return frame, usuario.first, clave.first
        page.wait_for_timeout(500)
    return None


def login(page, user: str, password: str) -> bool:
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    form = buscar_formulario(page)
    if form is None:
        diagnostico(page, "no aparece el formulario de login (usuario + contraseña)")
        return False
    frame, campo_usuario, campo_clave = form
    campo_usuario.fill(user)
    campo_clave.fill(password)
    boton = frame.locator(
        "button[type='submit'], input[type='submit'], button:has-text('Enviar')"
    )
    if boton.count():
        boton.first.click()
    else:
        campo_clave.press("Enter")
    page.wait_for_load_state("networkidle", timeout=60_000)
    return True


def url_documento(carpeta: str, titulo: str) -> str:
    return f"{BASE_URL}/{carpeta}/{quote_plus(titulo)}"


def main() -> int:
    user = os.environ.get("S_USER")
    password = os.environ.get("S_PASS")
    if not user or not password:
        print("Faltan S_USER / S_PASS", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nuevos = 0
    pendientes = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        if not login(page, user, password):
            return 2

        if "login" in page.url.lower():
            diagnostico(page, "el login parece haber fallado (sigue en la página de acceso)")
            return 3

        for carpeta, plantilla, base in DOCUMENTOS:
            semanas_probadas = semanas()
            conseguido = False
            for sem in semanas_probadas:
                if any(OUT_DIR.glob(f"S{sem}_{base}.*")):
                    conseguido = True
                    break
                url = url_documento(carpeta, plantilla.format(s=sem))
                resp = context.request.get(url)
                tipo = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.ok and tipo and tipo != "text/html":
                    destino = OUT_DIR / f"S{sem}_{base}{EXTENSIONES.get(tipo, '.bin')}"
                    destino.write_bytes(resp.body())
                    nuevos += 1
                    conseguido = True
                    print(f"Descargado: {destino.name}")
                    break
            if not conseguido:
                pendientes += 1
                print(f"No disponible: {base} (semanas probadas: {semanas_probadas})", file=sys.stderr)
        browser.close()

    print(f"Nuevos: {nuevos} | no disponibles: {pendientes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
