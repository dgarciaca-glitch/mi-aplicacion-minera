@echo off
REM Doble clic en Windows: crea el entorno la primera vez y abre la app
if not exist .venv (
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
streamlit run app.py
