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


def login(page, user: str, password: str) -> None:
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.get_by_placeholder("Usuario").fill(user)
    page.get_by_placeholder("Contraseña").fill(password)
    page.get_by_role("button", name="Enviar").click()
    page.wait_for_load_state("networkidle")


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
        login(page, user, password)

        if "login" in page.url.lower():
            print("El login parece haber fallado (sigue en la página de acceso).", file=sys.stderr)
            page.screenshot(path="error_login.png")
            return 2

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
