"""
paideia_scraper_v0.45.py — Respaldo de cursos de Paideia PUCP (Moodle).

QUÉ GUARDA (por cada curso):
  * Archivos   (mod/resource) -> TODOS los tipos (pdf, docx, pptx, xlsx, do...).
                                  Con --solo-pdf vuelve al modo de la v0.2.
  * Carpetas   (mod/folder)   -> como CARPETA normal (ya no .zip), respetando
                                  sus subcarpetas internas.
  * Foros      (mod/forum)    -> cada discusión como .html + sus adjuntos
                                  (Avisos, foros de consultas, etc.).
  * Tareas     (mod/assign)   -> enunciado, fechas, estado de tu entrega y
                                  archivos (del profe y tuyos) como .html + adjuntos.
  * Páginas    (mod/page)     -> contenido como .html.
  * Enlaces    (mod/url)      -> se resuelve el link real y se lista
                                  (no se descarga lo que hay detrás, todavía).
  * Etiquetas  (mod/label)    -> su texto queda en el índice, en su lugar.
  * Otros      (quiz, zoom, choice, ...) -> se guarda su página como .html
                                  (fechas, descripción, notas visibles).

  Además, en la carpeta del curso:
  * 00_INDICE.html -> "mini-portal": todas las secciones en el mismo orden que
                      Paideia, con etiquetas, descripciones, fechas y links a los
                      archivos locales. Ábrelo con doble clic.
  * enlaces.csv    -> todos los links externos (Zoom, YouTube, Drive, webs...).
  * errores.log    -> lo que no se pudo bajar y por qué.

ESTRUCTURA EN DISCO:
    <CARPETA_DESCARGAS>/
        2026-2/                                   <- ciclo (sale del título)
            ECONOMETRÍA INTERMEDIA_ MICRO (1ECO25-0821)/
                00_INDICE.html
                00 - General/
                01 - Semana 1/
                ...

CÓMO FUNCIONA:
  1. Abre un Chrome real con Selenium. TÚ inicias sesión a mano (el login PUCP
     es SSO; hacerlo manual es lo más robusto).
  2. Presionas Enter en la consola; el script copia las cookies a `requests`
     y cierra Chrome.
  3. Con `requests` recorre el/los cursos y descarga.
  Se puede re-ejecutar: lo que ya está en disco se salta (descarga incremental).

INSTALACIÓN (una vez):   pip install selenium requests beautifulsoup4
USO:
    python paideia_scraper_v0.45.py 69738
    python paideia_scraper_v0.45.py 69738 69733 --solo-pdf
    python paideia_scraper_v0.45.py 69738 --salida "D:/Universidad" --sin-foros
    python paideia_scraper_v0.45.py 69738 --max-mb 50      (salta grabaciones pesadas)
"""

import argparse
import csv
import html
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

VERSION = "0.45"

# ===========================================================================
# CONFIGURACIÓN — cambia aquí lo que quieras fijar por defecto
# ===========================================================================
# Carpeta raíz donde se crean <ciclo>/<curso>/. Por defecto: junto a este .py.
# Cuando tengas tu setup, pon una ruta fija, p. ej.:
#     CARPETA_DESCARGAS = Path(r"D:\Universidad\Paideia")
# (El flag --salida, si lo usas, tiene prioridad sobre esto.)
CARPETA_DESCARGAS = Path(__file__).resolve().parent / "descargas_paideia"

BASE = "https://paideiacursos.pucp.edu.pe"
REINTENTOS = 3        # intentos ante errores de red / servidor (5xx)
PAUSA_ENTRE = 0.4     # segundos entre descargas (no saturar el servidor)
TIMEOUT = 60          # segundos máximos por petición
MAX_PAGINAS_FORO = 50 # tope de páginas de discusiones por foro
# Límites de largo de nombre: Windows falla con rutas > 260 caracteres.
LARGO_CURSO, LARGO_SECCION, LARGO_ARCHIVO = 80, 60, 100


# ===========================================================================
# 1) LOGIN: Chrome visible + login manual -> cookies para requests
# ===========================================================================
def obtener_sesion(url_inicio: str) -> requests.Session:
    """Abre Chrome, espera el login manual y devuelve una requests.Session
    con las mismas cookies (o sea, ya autenticada)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    # Silenciar el "ruido" de Chrome en la consola (actualizador, DevTools...).
    opciones = webdriver.ChromeOptions()
    opciones.add_experimental_option("excludeSwitches", ["enable-logging"])
    opciones.add_argument("--log-level=3")
    opciones.add_argument("--disable-component-update")
    servicio = Service(log_output=subprocess.DEVNULL)

    driver = webdriver.Chrome(options=opciones, service=servicio)
    try:
        driver.get(url_inicio)
        print("\n>>> Inicia sesión en la ventana de Chrome que se abrió.")
        print(">>> Cuando veas la página del curso, vuelve aquí y presiona Enter.")
        input()
        if "paideiacursos.pucp.edu.pe" not in driver.current_url:
            sys.exit(f"[ERROR] Parece que no terminaste el login (URL: {driver.current_url}).")

        sesion = requests.Session()
        # Mismo User-Agent que Chrome: algunos servidores atan la sesión a él.
        sesion.headers["User-Agent"] = driver.execute_script("return navigator.userAgent")
        for c in driver.get_cookies():  # incluye MoodleSession
            sesion.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
        return sesion
    finally:
        driver.quit()


# ===========================================================================
# 2) UTILIDADES GENERALES
# ===========================================================================
class SesionExpirada(Exception):
    """Moodle nos mandó al login: no tiene sentido seguir."""


def limpiar_nombre(texto: str, max_len: int = LARGO_ARCHIVO) -> str:
    """Texto -> nombre válido de archivo/carpeta en Windows."""
    texto = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    if len(texto) > max_len:  # recorta pero conserva la extensión
        raiz, ext = os.path.splitext(texto)
        ext = ext if len(ext) <= 6 else ""
        texto = raiz[: max_len - len(ext)].rstrip(" .") + ext
    return texto or "sin_nombre"


def separar_ciclo(titulo: str):
    """'2026-2 ECONOMÍA DEL DESARROLLO (1ECO22-0721)'
         -> ('2026-2', 'ECONOMÍA DEL DESARROLLO (1ECO22-0721)').
    Si el título no empieza con un ciclo, va a la carpeta 'sin_ciclo'."""
    m = re.match(r"\s*(\d{4}-[0-9A-Za-z])\s+(.+)", titulo)
    return (m.group(1), m.group(2).strip()) if m else ("sin_ciclo", titulo.strip())


def es_login(url: str) -> bool:
    """True si la URL es el login (sesión expirada)."""
    p = urlparse(url)
    return p.netloc == "pandora.pucp.edu.pe" or (
        p.netloc == urlparse(BASE).netloc and (p.path.startswith("/login/") or "/auth/shibboleth" in p.path)
    )


def L(p) -> str:
    """Ruta para operaciones de disco. En Windows le antepone '\\\\?\\', que
    desactiva el límite clásico de 260 caracteres (MAX_PATH): sin esto, los
    archivos dentro de carpetas con nombres largos fallan con '[Errno 2]'."""
    s = str(Path(p).resolve())
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + s
    return s


def reparar_utf8(texto: str) -> str:
    """Arregla el "mojibake": 'EconomÃ­as' -> 'Economías'.
    Pasa cuando un texto en UTF-8 se leyó como Latin-1 (es lo que hace
    `requests` con las cabeceras HTTP). Si el texto ya estaba bien, al
    intentar revertirlo falla y se devuelve tal cual."""
    try:
        arreglado = texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto
    return arreglado


def nombre_desde_respuesta(r: requests.Response, respaldo: str) -> str:
    """Nombre real del archivo, en orden de confianza:
      1. filename*=UTF-8''...  (codificado de forma estándar, siempre correcto)
      2. la URL final de pluginfile.php (va en %XX, se decodifica bien)
      3. filename="..." de la cabecera: Moodle lo manda en UTF-8 crudo y
         `requests` lo lee como Latin-1 -> hay que repararlo (bug de la v0.4:
         salían nombres como 'EconomÃ­as a escala.pdf')."""
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1))
    if "pluginfile.php" in r.url:
        return unquote(Path(urlparse(r.url).path).name)
    m = re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return reparar_utf8(unquote(m.group(1)))
    return respaldo


def ruta_relativa(desde_archivo: Path, hacia: Path) -> str:
    """Link relativo (para HTML) desde un .html a otro archivo local."""
    rel = os.path.relpath(hacia, desde_archivo.parent)
    return quote(Path(rel).as_posix())


class Registro:
    """Lleva la cuenta de lo hecho y escribe errores.log."""

    def __init__(self):
        self.cuenta = {"ok": 0, "existe": 0, "saltado": 0, "error": 0}
        self.errores = []

    def anotar(self, estado: str, texto: str):
        self.cuenta[estado] = self.cuenta.get(estado, 0) + 1
        print(f"  [{estado:7}] {texto}")

    def error(self, texto: str):
        self.cuenta["error"] += 1
        self.errores.append(texto)
        print(f"  [error  ] {texto}")


# ===========================================================================
# 3) CLIENTE HTTP con reintentos sensatos
# ===========================================================================
class Cliente:
    def __init__(self, sesion: requests.Session, registro: Registro, max_mb: float = 0):
        self.s = sesion
        self.reg = registro
        self.max_bytes = int(max_mb * 1024 * 1024)  # 0 = sin límite

    def get(self, url: str, **kw) -> requests.Response:
        """GET con reintentos SOLO ante fallos de red o errores 5xx.
        Un 404/403 no se reintenta (nunca va a funcionar): se lanza HTTPError."""
        kw.setdefault("timeout", TIMEOUT)
        for intento in range(1, REINTENTOS + 1):
            try:
                r = self.s.get(url, **kw)
                if es_login(r.url) or es_login(r.headers.get("Location", "")):
                    raise SesionExpirada()
                if r.status_code >= 500 and intento < REINTENTOS:
                    raise requests.HTTPError(f"{r.status_code} del servidor")
                r.raise_for_status()
                return r
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", 500) if e.response is not None else 500
                if status < 500 or intento == REINTENTOS:
                    raise
                print(f"    reintento {intento}/{REINTENTOS}: {e}")
            except (requests.ConnectionError, requests.Timeout) as e:
                if intento == REINTENTOS:
                    raise
                print(f"    reintento {intento}/{REINTENTOS}: {e}")
            time.sleep(2 * intento)

    def sopa(self, url: str) -> BeautifulSoup:
        return BeautifulSoup(self.get(url).text, "html.parser")

    def descargar(self, url: str, carpeta: Path, nombre: str = None, solo_pdf=False):
        """Descarga `url` dentro de `carpeta`.
        - `nombre`: si no se da, se usa el nombre real que manda el servidor.
        Devuelve (estado, ruta_local | None, respuesta_html | None):
          estado = ok / existe / saltado ; si el servidor responde una página
          HTML en vez de un archivo, se devuelve esa sopa para que el llamador
          busque el archivo dentro (pasa con recursos "incrustados")."""
        with self.get(url, stream=True) as r:
            tipo = r.headers.get("Content-Type", "").lower()
            if "text/html" in tipo:
                return "html", None, BeautifulSoup(r.text, "html.parser")
            archivo = limpiar_nombre(nombre or nombre_desde_respuesta(r, "archivo"))
            destino = carpeta / archivo
            if solo_pdf and "pdf" not in tipo and not archivo.lower().endswith(".pdf"):
                return "saltado", destino, None
            if os.path.exists(L(destino)) and os.path.getsize(L(destino)) > 0:
                return "existe", destino, None
            # Límite de tamaño (--max-mb): útil para saltar grabaciones de video.
            tam = int(r.headers.get("Content-Length") or 0)
            if self.max_bytes and tam > self.max_bytes:
                print(f"    {archivo}: {tam / 2**20:.0f} MB > límite --max-mb, se salta")
                return "saltado", destino, None
            os.makedirs(L(carpeta), exist_ok=True)
            temporal = destino.with_name(destino.name + ".part")
            with open(L(temporal), "wb") as f:  # se escribe a .part y luego se renombra
                for bloque in r.iter_content(64 * 1024):
                    f.write(bloque)
            os.replace(L(temporal), L(destino))
            time.sleep(PAUSA_ENTRE)
            return "ok", destino, None


# ===========================================================================
# 4) GUARDAR UNA PÁGINA DE MOODLE COMO .HTML AUTÓNOMO
# ===========================================================================
ESTILO = """<style>
body{font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;line-height:1.5;color:#222}
img{max-width:100%;height:auto} table{border-collapse:collapse} td,th{border:1px solid #ccc;padding:4px 8px}
.origen{font-size:12px;color:#777;border-bottom:1px solid #ddd;padding-bottom:8px;margin-bottom:16px}
</style>"""


def guardar_pagina(cli: Cliente, reg: Registro, sopa: BeautifulSoup, url_origen: str,
                   destino_html: Path, carpeta_adjuntos: Path, titulo: str, solo_pdf=False):
    """Toma el contenido principal de una página de Moodle (#region-main),
    descarga TODOS los archivos que referencia (links e imágenes de
    pluginfile.php), reescribe esos links a las copias locales y guarda un
    .html que se abre sin internet."""
    if os.path.exists(L(destino_html)):
        reg.anotar("existe", destino_html.name)
        return
    principal = sopa.select_one("#region-main") or sopa.select_one("[role=main]") or sopa.body or sopa
    for basura in principal.select("script, style, noscript, form .btn, .modal"):
        basura.decompose()

    for etiqueta, attr in (("a", "href"), ("img", "src")):
        for el in principal.find_all(etiqueta):
            enlace = el.get(attr)
            if not enlace or "pluginfile.php" not in enlace or "/user/icon/" in enlace:
                continue  # solo archivos del curso (no fotos de perfil, iconos, etc.)
            absoluto = urljoin(url_origen, enlace)
            try:
                estado, ruta, _ = cli.descargar(absoluto, carpeta_adjuntos, solo_pdf=solo_pdf and etiqueta == "a")
                if ruta and estado in ("ok", "existe"):
                    el[attr] = ruta_relativa(destino_html, ruta)
                    if estado == "ok":
                        reg.anotar("ok", f"{carpeta_adjuntos.name}/{ruta.name}")
            except SesionExpirada:
                raise
            except Exception as e:  # un adjunto roto no debe tumbar la página
                reg.error(f"adjunto {absoluto} -> {e}")
                el[attr] = absoluto  # al menos que quede el link original

    os.makedirs(L(destino_html.parent), exist_ok=True)
    Path(L(destino_html)).write_text(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(titulo)}</title>{ESTILO}</head><body>"
        f"<div class='origen'>Respaldo de <a href='{html.escape(url_origen)}'>{html.escape(url_origen)}</a></div>"
        f"{principal.decode_contents() if hasattr(principal, 'decode_contents') else principal}</body></html>",
        encoding="utf-8",
    )
    reg.anotar("ok", destino_html.name)


# ===========================================================================
# 5) LEER LA PÁGINA DEL CURSO (secciones, actividades, etiquetas, orden)
# ===========================================================================
def html_interno(el) -> str:
    return el.decode_contents().strip() if el else ""


def parsear_actividad(li) -> dict:
    """Extrae de un <li class="activity ..."> todo lo que se ve en el curso."""
    # Tipo e id: Moodle los pone en la clase "modtype_xxx" y en id="module-NNN".
    tipo = next((c[8:] for c in li.get("class", []) if c.startswith("modtype_")), None)
    mid = (re.search(r"module-(\d+)", li.get("id", "")) or [None, None])[1]
    enlace = li.select_one('a[href*="/mod/"]')
    if enlace and (not tipo or not mid):
        m = re.search(r"/mod/(\w+)/view\.php\?id=(\d+)", enlace["href"])
        if m:
            tipo, mid = tipo or m.group(1), mid or m.group(2)

    nombre_el = li.select_one(".instancename") or enlace
    nombre = ""
    if nombre_el:
        copia = BeautifulSoup(str(nombre_el), "html.parser")
        for oculto in copia.select(".accesshide"):  # quita "Archivo", "Carpeta"...
            oculto.decompose()
        nombre = copia.get_text(" ", strip=True)

    # Descripción visible bajo la actividad (o el contenido entero si es etiqueta).
    desc = li.select_one(".activity-altcontent, .contentafterlink, .contentwithoutlink, .activity-description, .description")
    fechas = li.select_one('[data-region="activity-dates"], .activity-dates')
    icono = (li.select_one("img.activityicon, img.iconlarge") or {}).get("src", "")
    return {
        "tipo": tipo or "desconocido",
        "id": mid,
        "nombre": nombre or (desc.get_text(" ", strip=True)[:60] if desc else "sin nombre"),
        "descripcion_html": html_interno(desc),
        "fechas": fechas.get_text(" ", strip=True) if fechas else "",
        "icono": icono,
        "locales": [],   # se llena al descargar: rutas locales
        "externo": "",   # link externo (mod/url)
    }


def parsear_secciones(sopa: BeautifulSoup):
    secciones = []
    for li in sopa.select("li.section, li.course-section"):
        cab = li.select_one(".sectionname, h3")
        resumen = li.select_one(".summarytext, .summary, .section_summary")
        actividades = [parsear_actividad(a) for a in li.select("li.activity")]
        enlace_sec = li.select_one('a[href*="section="], a[href*="section.php"]')
        secciones.append({
            "nombre": cab.get_text(" ", strip=True) if cab else "Sin nombre",
            "resumen_html": html_interno(resumen),
            "actividades": actividades,
            "enlace": enlace_sec["href"] if enlace_sec else None,
        })
    return secciones


def leer_curso(cli: Cliente, curso_id: str):
    url = f"{BASE}/course/view.php?id={curso_id}"
    sopa = cli.sopa(url)
    h1 = sopa.find("h1")
    titulo = h1.get_text(" ", strip=True) if h1 else f"curso_{curso_id}"
    secciones = parsear_secciones(sopa)

    # Formato "una sección por página": la portada solo trae enlaces a cada
    # sección. Si una sección viene vacía pero tiene enlace, se abre aparte.
    for sec in secciones:
        if not sec["actividades"] and sec["enlace"]:
            sub = parsear_secciones(cli.sopa(urljoin(url, sec["enlace"])))
            for s in sub:
                if s["actividades"]:
                    sec["actividades"] = s["actividades"]
                    sec["resumen_html"] = sec["resumen_html"] or s["resumen_html"]
                    break
    return titulo, secciones


# ===========================================================================
# 6) PROCESAR CADA TIPO DE ACTIVIDAD
# ===========================================================================
def procesar_resource(cli, reg, act, carpeta, op):
    # Filtro rápido por icono (".../f/pdf") para no pedir lo que no es PDF.
    if op.solo_pdf and act["icono"] and "/f/" in act["icono"] and "/f/pdf" not in act["icono"]:
        reg.anotar("saltado", f"{act['nombre']} (no es PDF)")
        return
    url = f"{BASE}/mod/resource/view.php?id={act['id']}&redirect=1"
    estado, ruta, sopa = cli.descargar(url, carpeta, solo_pdf=op.solo_pdf)
    if estado == "html":
        # Recurso "incrustado": el archivo está enlazado dentro de la página.
        enlace = sopa.select_one('#region-main a[href*="pluginfile.php"], object[data*="pluginfile.php"], iframe[src*="pluginfile.php"]')
        if not enlace:
            raise RuntimeError("la página del recurso no contiene ningún archivo")
        archivo = enlace.get("href") or enlace.get("data") or enlace.get("src")
        estado, ruta, _ = cli.descargar(urljoin(BASE, archivo), carpeta, solo_pdf=op.solo_pdf)
    if ruta and estado != "saltado":
        act["locales"].append(ruta)
    reg.anotar(estado, f"{carpeta.name}/{ruta.name if ruta else act['nombre']}")


def procesar_folder(cli, reg, act, carpeta, op):
    """Carpeta -> carpeta real. Se listan los archivos de la página de la
    carpeta y se baja cada uno, respetando sus subcarpetas internas."""
    url = f"{BASE}/mod/folder/view.php?id={act['id']}"
    sopa = cli.sopa(url)
    destino = carpeta / limpiar_nombre(act["nombre"], LARGO_SECCION)
    act["locales"].append(destino)
    vistos = set()
    for a in sopa.select('a[href*="pluginfile.php"][href*="mod_folder"]'):
        href = urljoin(url, a["href"]).split("?")[0]
        if href in vistos:
            continue
        vistos.add(href)
        # .../mod_folder/content/<rev>/sub/carpeta/archivo.pdf -> sub/carpeta
        partes = unquote(urlparse(href).path).split("/mod_folder/content/", 1)[-1].split("/")[1:]
        subcarpeta = destino.joinpath(*[limpiar_nombre(p, LARGO_SECCION) for p in partes[:-1]])
        try:
            estado, ruta, _ = cli.descargar(href + "?forcedownload=1", subcarpeta,
                                            nombre=partes[-1], solo_pdf=op.solo_pdf)
            reg.anotar(estado, f"{destino.name}/{'/'.join(partes)}")
        except SesionExpirada:
            raise
        except Exception as e:
            reg.error(f"{destino.name}/{'/'.join(partes)} -> {e}")
    if not vistos:
        reg.anotar("saltado", f"{destino.name} (carpeta vacía)")


def procesar_url(cli, reg, act, carpeta, op, enlaces):
    """Enlace externo: se averigua a dónde apunta sin seguirlo."""
    url = f"{BASE}/mod/url/view.php?id={act['id']}&redirect=1"
    r = cli.get(url, allow_redirects=False)
    destino = r.headers.get("Location", "")
    if not destino or destino.startswith(BASE + "/mod/url"):
        sopa = BeautifulSoup(cli.get(f"{BASE}/mod/url/view.php?id={act['id']}").text, "html.parser")
        a = sopa.select_one(".urlworkaround a, #region-main a[href^='http']:not([href*='paideiacursos'])")
        destino = a["href"] if a else ""
    act["externo"] = destino
    enlaces.append((carpeta.name, act["nombre"], destino))
    reg.anotar("ok", f"enlace: {act['nombre']} -> {destino or '(no encontrado)'}")


def procesar_forum(cli, reg, act, carpeta, op):
    """Foro -> carpeta con una .html por discusión (+ adjuntos)."""
    base_foro = carpeta / limpiar_nombre(f"{act['nombre']} (foro)", LARGO_SECCION)
    adjuntos = base_foro / "adjuntos"
    act["locales"].append(base_foro)
    vistas, pagina = {}, 0
    while pagina < MAX_PAGINAS_FORO:
        url = f"{BASE}/mod/forum/view.php?id={act['id']}&p={pagina}"
        sopa = cli.sopa(url)
        nuevas = 0
        for a in sopa.select('a[href*="/mod/forum/discuss.php?d="]'):
            d = re.search(r"d=(\d+)", a["href"]).group(1)
            titulo = a.get_text(" ", strip=True)
            if d not in vistas and titulo:
                vistas[d] = titulo
                nuevas += 1
        if pagina == 0 and not vistas:
            # Foro de "debate único" u otro formato: se guarda la página tal cual.
            guardar_pagina(cli, reg, sopa, url, base_foro / "foro.html", adjuntos, act["nombre"])
            return
        if not nuevas:
            break
        pagina += 1

    for n, (d, titulo) in enumerate(vistas.items(), 1):
        url = f"{BASE}/mod/forum/discuss.php?d={d}"
        try:
            guardar_pagina(cli, reg, cli.sopa(url), url,
                           base_foro / limpiar_nombre(f"{n:03d} - {titulo}.html"), adjuntos, titulo)
        except SesionExpirada:
            raise
        except Exception as e:
            reg.error(f"discusión {url} -> {e}")


def procesar_pagina_generica(cli, reg, act, carpeta, op, sufijo):
    """Tareas, páginas, cuestionarios, zoom, etc.: se guarda la página."""
    url = f"{BASE}/mod/{act['tipo']}/view.php?id={act['id']}"
    nombre = limpiar_nombre(f"{act['nombre']} ({sufijo})")
    destino = carpeta / f"{nombre}.html"
    guardar_pagina(cli, reg, cli.sopa(url), url, destino, carpeta / f"{nombre} - adjuntos",
                   act["nombre"], solo_pdf=False)
    act["locales"].append(destino)


SUFIJOS = {"assign": "tarea", "page": "página", "quiz": "cuestionario", "zoom": "zoom",
           "choice": "votación", "feedback": "encuesta", "lesson": "lección", "book": "libro",
           "glossary": "glosario", "workshop": "taller", "lti": "herramienta externa"}


def procesar_curso(cli: Cliente, reg: Registro, curso_id: str, op):
    titulo, secciones = leer_curso(cli, curso_id)
    ciclo, nombre_curso = separar_ciclo(titulo)
    raiz = op.salida / limpiar_nombre(ciclo, 20) / limpiar_nombre(nombre_curso, LARGO_CURSO)
    print(f"\n=== {titulo}\n    -> {raiz}")
    enlaces = []
    errores_previos = len(reg.errores)

    for i, sec in enumerate(secciones):
        carpeta = raiz / f"{i:02d} - {limpiar_nombre(sec['nombre'], LARGO_SECCION)}"
        if sec["actividades"]:
            print(f"\n  # {sec['nombre']}")
        for act in sec["actividades"]:
            t = act["tipo"]
            try:
                if t == "label" or not act["id"]:
                    continue  # etiqueta: su texto va al índice
                if t == "resource":
                    procesar_resource(cli, reg, act, carpeta, op)
                elif t == "folder":
                    procesar_folder(cli, reg, act, carpeta, op)
                elif t == "url":
                    procesar_url(cli, reg, act, carpeta, op, enlaces)
                elif t == "forum":
                    if op.sin_foros:
                        reg.anotar("saltado", f"{act['nombre']} (foro, --sin-foros)")
                    else:
                        procesar_forum(cli, reg, act, carpeta, op)
                else:
                    procesar_pagina_generica(cli, reg, act, carpeta, op, SUFIJOS.get(t, t))
            except SesionExpirada:
                raise
            except Exception as e:
                # Un fallo en una actividad NO detiene el resto del curso.
                reg.error(f"[{t}] {act['nombre']} (id {act['id']}) -> {e}")
                reg.errores.append(traceback.format_exc())

    escribir_indice(raiz, titulo, curso_id, secciones)
    with open(raiz / "enlaces.csv", "w", newline="", encoding="utf-8-sig") as f:  # -sig: Excel lee bien las tildes
        w = csv.writer(f, delimiter=";")
        w.writerow(["seccion", "nombre", "url"])
        w.writerows(enlaces)
    # Solo los errores de ESTE curso (el registro es común a todos los cursos).
    errores_curso = reg.errores[errores_previos:]
    if errores_curso:
        (raiz / "errores.log").write_text("\n".join(errores_curso), encoding="utf-8")
    elif (raiz / "errores.log").exists():
        (raiz / "errores.log").unlink()  # corrida limpia: se borra el log viejo


# ===========================================================================
# 7) ÍNDICE HTML (mini-portal offline)
# ===========================================================================
def escribir_indice(raiz: Path, titulo: str, curso_id: str, secciones):
    destino = raiz / "00_INDICE.html"
    partes = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(titulo)}</title>{ESTILO}"
              "<style>.sec{border:1px solid #ddd;border-radius:8px;padding:8px 16px;margin:16px 0}"
              ".act{margin:10px 0}.tipo{font-size:11px;color:#fff;background:#888;border-radius:4px;padding:1px 6px;margin-right:6px}"
              ".fechas,.desc{font-size:13px;color:#555;margin-left:12px}</style></head><body>",
              f"<h1>{html.escape(titulo)}</h1>",
              f"<div class='origen'>Original: <a href='{BASE}/course/view.php?id={curso_id}'>Paideia</a> · "
              f"respaldo {time.strftime('%Y-%m-%d %H:%M')} · v{VERSION}</div>"]
    for sec in secciones:
        if not sec["actividades"] and not sec["resumen_html"]:
            continue
        partes.append(f"<div class='sec'><h2>{html.escape(sec['nombre'])}</h2>{sec['resumen_html']}")
        for act in sec["actividades"]:
            if act["tipo"] == "label":
                partes.append(f"<div class='act'>{act['descripcion_html']}</div>")
                continue
            if act["locales"]:
                href = ruta_relativa(destino, act["locales"][0])
            else:
                href = act["externo"] or f"{BASE}/mod/{act['tipo']}/view.php?id={act['id']}"
            partes.append(f"<div class='act'><span class='tipo'>{act['tipo']}</span>"
                          f"<a href='{html.escape(href)}'>{html.escape(act['nombre'])}</a>")
            if act["externo"]:
                partes.append(f" <small>→ {html.escape(act['externo'])}</small>")
            if act["fechas"]:
                partes.append(f"<div class='fechas'>{html.escape(act['fechas'])}</div>")
            if act["descripcion_html"]:
                partes.append(f"<div class='desc'>{act['descripcion_html']}</div>")
            partes.append("</div>")
        partes.append("</div>")
    partes.append("</body></html>")
    raiz.mkdir(parents=True, exist_ok=True)
    destino.write_text("".join(partes), encoding="utf-8")
    print(f"\n  Índice: {destino}")


# ===========================================================================
# 8) PROGRAMA PRINCIPAL
# ===========================================================================
def main():
    p = argparse.ArgumentParser(description=f"Respaldo de cursos de Paideia PUCP (v{VERSION}).")
    p.add_argument("cursos", nargs="+", help="Uno o más id de curso (course/view.php?id=XXXX)")
    p.add_argument("--salida", type=Path, default=CARPETA_DESCARGAS,
                   help="Carpeta raíz de descargas (por defecto: CARPETA_DESCARGAS del preámbulo)")
    p.add_argument("--solo-pdf", action="store_true", help="De los archivos sueltos y carpetas, bajar solo PDFs")
    p.add_argument("--sin-foros", action="store_true", help="No respaldar foros (más rápido)")
    p.add_argument("--max-mb", type=float, default=0,
                   help="Saltar archivos más pesados que N MB (p. ej. grabaciones). 0 = sin límite")
    op = p.parse_args()

    sesion = obtener_sesion(f"{BASE}/course/view.php?id={op.cursos[0]}")
    reg = Registro()
    cli = Cliente(sesion, reg, op.max_mb)
    try:
        for curso_id in op.cursos:
            try:
                procesar_curso(cli, reg, curso_id, op)
            except SesionExpirada:
                raise
            except Exception as e:
                reg.error(f"curso {curso_id} -> {e}")
                traceback.print_exc()
    except SesionExpirada:
        print("\n[ERROR] La sesión expiró. Vuelve a ejecutar: lo ya descargado se saltará.")

    c = reg.cuenta
    print(f"\nListo (v{VERSION}). Nuevos: {c['ok']} | Ya existían: {c['existe']} | "
          f"Saltados: {c['saltado']} | Errores: {c['error']}")
    if c["error"]:
        print("Detalle de errores en errores.log dentro de la carpeta de cada curso.")


if __name__ == "__main__":
    main()
