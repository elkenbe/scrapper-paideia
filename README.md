# scrapper-paideia
Script en Python que hace un **respaldo local de cursos de Paideia PUCP** (Moodle): descarga el material, guarda foros, tareas y páginas como HTML y arma un índice navegable sin conexión. Versión **0.45**.

## Instalación

Requiere Python 3 y Google Chrome:

```
pip install selenium requests beautifulsoup4
```

## Uso

El `id` del curso es el número que aparece en la URL: `.../course/view.php?id=XXXXX`.

```
python paideia_scraper_v0_45.py 69738
python paideia_scraper_v0_45.py 69738 69733 --solo-pdf
python paideia_scraper_v0_45.py 69738 --salida "D:/Universidad" --sin-foros
python paideia_scraper_v0_45.py 69738 --max-mb 50
```

| Opción | Efecto |
|---|---|
| `cursos` (uno o más ids) | Cursos a respaldar |
| `--salida RUTA` | Carpeta raíz de descargas (tiene prioridad sobre `CARPETA_DESCARGAS`) |
| `--solo-pdf` | De archivos sueltos y carpetas, baja solo PDF |
| `--sin-foros` | No respalda foros (más rápido) |
| `--max-mb N` | Salta archivos de más de N MB, por ejemplo grabaciones; 0 = sin límite |

Sin `--salida`, descarga en `descargas_paideia/` junto al script. Para fijar otra ruta por defecto, edita `CARPETA_DESCARGAS` en la sección de configuración.

## Cómo funciona

1. Abre un Chrome real con Selenium. **Tú inicias sesión a mano** (el login PUCP es SSO).
2. Cuando ves la página del curso, vuelves a la consola y presionas Enter. El script copia las cookies a `requests` y cierra Chrome.
3. Con `requests` recorre los cursos y descarga todo.

Es **incremental**: puedes volver a ejecutarlo y lo que ya está en disco se salta. Si la sesión expira, el script se detiene con un aviso; vuelve a correrlo y continúa donde quedó.

## Qué guarda

| Tipo en Moodle | Resultado |
|---|---|
| Archivos (`resource`) | Todos los tipos (pdf, docx, pptx, xlsx, do...); solo PDF con `--solo-pdf` |
| Carpetas (`folder`) | Carpeta normal, con sus subcarpetas internas |
| Foros (`forum`) | Cada discusión como `.html` más sus adjuntos |
| Tareas (`assign`) | Enunciado, fechas, estado de tu entrega y archivos, como `.html` más adjuntos |
| Páginas (`page`) | Contenido como `.html` |
| Enlaces (`url`) | Se resuelve el link real y se lista; **no** se descarga lo que hay detrás |
| Etiquetas (`label`) | Su texto queda en el índice |
| Otros (quiz, zoom, choice, etc.) | Su página se guarda como `.html` (fechas, descripción, notas visibles) |

## Estructura en disco

```
<CARPETA_DESCARGAS>/
└── 2026-2/                                  <- ciclo (sale del título del curso)
    └── ECONOMETRÍA INTERMEDIA_ MICRO (1ECO25-0821)/
        ├── 00_INDICE.html
        ├── enlaces.csv
        ├── errores.log                      <- solo si hubo errores
        ├── 00 - General/
        ├── 01 - Semana 1/
        └── ...
```

- **`00_INDICE.html`**: mini-portal con las secciones en el mismo orden que Paideia, con etiquetas, descripciones, fechas y links a los archivos locales. Se abre con doble clic.
- **`enlaces.csv`**: todos los links externos (Zoom, YouTube, Drive, webs). Separador `;` y codificación `utf-8-sig`, para que Excel lea bien las tildes.
- **`errores.log`**: lo que no se pudo descargar y por qué. En una corrida limpia, el log anterior se borra.

Si el título del curso no empieza con un ciclo (por ejemplo `2026-2`), el curso va a la carpeta `sin_ciclo`.

## Parámetros ajustables

Al inicio del script:

| Constante | Valor | Para qué sirve |
|---|---|---|
| `REINTENTOS` | 3 | Intentos ante errores de red o del servidor (5xx); un 404 o 403 no se reintenta |
| `PAUSA_ENTRE` | 0.4 s | Pausa entre descargas, para no saturar el servidor |
| `TIMEOUT` | 60 s | Tiempo máximo por petición |
| `MAX_PAGINAS_FORO` | 50 | Tope de páginas de discusiones por foro |
| `LARGO_CURSO`, `LARGO_SECCION`, `LARGO_ARCHIVO` | 80, 60, 100 | Largo máximo de nombres, para no pasar el límite de rutas de Windows |

## Detalles técnicos

- Los archivos se escriben primero como `.part` y luego se renombran, así una descarga interrumpida no deja archivos a medias.
- En Windows, a las rutas se les antepone `\\?\` para evitar el límite de 260 caracteres.
- Se corrige el "mojibake" de los nombres de archivo (por ejemplo `EconomÃ­as` → `Economías`), problema de la versión 0.4.
- Un fallo en una actividad no detiene el resto del curso: se registra y continúa.

## Limitaciones

- Los enlaces externos (`url`) solo se listan; su contenido no se descarga.
- Requiere un navegador Chrome disponible para el inicio de sesión manual.
- Está pensado para Paideia PUCP (`paideiacursos.pucp.edu.pe`); el dominio está fijado en la constante `BASE`.
- Úsalo solo con cursos en los que estás matriculado y para tu uso personal.
