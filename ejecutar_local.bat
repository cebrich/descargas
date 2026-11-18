@echo off
rem Descarga los documentos de eSTILA desde este PC y sube los nuevos a GitHub.
rem Requiere: Python (py), Git, Google Chrome, y las variables de usuario S_USER y S_PASS.
cd /d "%~dp0"

rem Usa el Chrome instalado y con ventana visible (el modo oculto da "Access Denied").
if not defined NAVEGADOR set NAVEGADOR=chrome
if not defined HEADLESS set HEADLESS=0

if not exist .venv\Scripts\python.exe py -m venv .venv

.venv\Scripts\python -c "import playwright" 2>nul || (
    .venv\Scripts\python -m pip install -r requirements.txt
)

.venv\Scripts\python descargar_pdfs.py
if errorlevel 1 (
    echo La descarga ha fallado. No se sube nada.
    exit /b 1
)

git add descargas
git diff --staged --quiet || (git commit -m "Nuevos PDFs" && git pull --rebase --autostash -X theirs && git push)
