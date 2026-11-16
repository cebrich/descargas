"""Descarga PDFs de estila.selae.es tras iniciar sesión.

Credenciales por variables de entorno (nunca en el código):
  SELAE_USER, SELAE_PASS
Opcional:
  START_URL  -> página de eSTILA donde están los enlaces a los PDFs
  OUT_DIR    -> carpeta de destino (por defecto "descargas")
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://gus-forms.lbext.selae.es/gus/loginSELAE"
START_URL = os.environ.get("START_URL", "https://estila.selae.es")
OUT_DIR = Path(os.environ.get("OUT_DIR", "descargas"))


def login(page, user: str, password: str) -> None:
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.get_by_placeholder("Usuario").fill(user)
    page.get_by_placeholder("Contraseña").fill(password)
    page.get_by_role("button", name="Enviar").click()
    page.wait_for_load_state("networkidle")


def pdf_links(page) -> list[str]:
    # TODO: ajustar cuando sepamos cómo se llega a los PDFs dentro de eSTILA
    # (menús, filtros, fechas...). De momento busca enlaces .pdf en START_URL.
    page.goto(START_URL, wait_until="networkidle")
    hrefs = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.getAttribute('href'))"
    )
    links = [urljoin(page.url, h) for h in hrefs if h and ".pdf" in h.lower()]
    return sorted(set(links))


def main() -> int:
    user = os.environ.get("SELAE_USER")
    password = os.environ.get("SELAE_PASS")
    if not user or not password:
        print("Faltan SELAE_USER / SELAE_PASS", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nuevos = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        login(page, user, password)

        if "login" in page.url.lower():
            print("El login parece haber fallado (sigue en la página de acceso).", file=sys.stderr)
            page.screenshot(path="error_login.png")
            return 2

        for url in pdf_links(page):
            nombre = re.sub(r"[^\w.\-]", "_", url.split("/")[-1].split("?")[0]) or "documento.pdf"
            destino = OUT_DIR / nombre
            if destino.exists():
                continue
            resp = context.request.get(url)
            if resp.ok and resp.body()[:4] == b"%PDF":
                destino.write_bytes(resp.body())
                nuevos += 1
                print(f"Descargado: {nombre}")
            else:
                print(f"No se pudo descargar {url} ({resp.status})", file=sys.stderr)
        browser.close()

    print(f"PDFs nuevos: {nuevos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
