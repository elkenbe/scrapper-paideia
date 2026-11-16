@echo off
setlocal
chcp 65001 >nul
title Paideia Scraper v0.45

echo ================================================
echo   PAIDEIA SCRAPER v0.45
echo ================================================
echo.
echo El ID del curso es el numero al final de la URL:
echo   https://paideiacursos.pucp.edu.pe/course/view.php?id=XXXXX
echo Puedes poner varios separados por espacio (un solo login):
echo   69738 69733 70001
echo.
set "CURSOS="
set /p "CURSOS=ID(s) del curso: "
if "%CURSOS%"=="" (
    echo [ERROR] No escribiste ningun ID.
    pause
    exit /b 1
)

echo.
echo ------------------------------------------------
echo   FLAGS OPCIONALES (puedes combinar varios)
echo ------------------------------------------------
echo   --solo-pdf
echo       Solo baja PDFs (archivos sueltos y dentro de carpetas).
echo       Sin este flag se baja TODO: pdf, docx, pptx, xlsx, .do, etc.
echo.
echo   --sin-foros
echo       No respalda los foros (Avisos, consultas). Mas rapido.
echo.
echo   --max-mb N
echo       Salta archivos de mas de N megas (p. ej. grabaciones de video).
echo       Ejemplo:  --max-mb 50
echo.
echo   --salida "RUTA"
echo       Carpeta raiz de descargas. Por defecto: "descargas_paideia"
echo       junto al .py (se cambia en CARPETA_DESCARGAS del preambulo).
echo       Ejemplo:  --salida "D:\Universidad"
echo.
echo   Ejemplo combinado:  --sin-foros --max-mb 50
echo   (Deja vacio y presiona Enter para no agregar ninguno)
echo ------------------------------------------------
set "FLAGS="
set /p "FLAGS=Flags: "

echo.
echo Ejecutando: python paideia_scraper_v0.45.py %CURSOS% %FLAGS%
echo.
python "%~dp0paideia_scraper_v0.45.py" %CURSOS% %FLAGS%

echo.
pause
