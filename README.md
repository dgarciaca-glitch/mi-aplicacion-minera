# Digitalizador La Arena

Interfaz (Streamlit) + motor de limpieza (`arena_core`) que toma CSV/Excel sucios de operación minera y devuelve
**CSV corregidos, ordenados y trazables**, una **base consolidada** y un **libro listo para Google Sheets**.

## Ejecutar en VS Code
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
streamlit run app.py             # abre http://localhost:8501
```
En Windows también: doble clic en `run_app.bat`. En VS Code: *Run and Debug → "Streamlit: app.py"*.

Sin interfaz (misma lógica): `python -m arena_core.cli "C:\ruta\archivos" --out salida --sep ;`

## Qué hace el motor (`arena_core/`)
| Módulo | Responsabilidad |
|---|---|
| `ingest.py` | CSV (detecta codificación y separador), Excel (todas las hojas), ZIP y carpetas |
| `structure.py` | Detecta títulos sobre la tabla, encabezados de varias filas, tablas lado a lado, jerarquías de tabla dinámica, filas de grupo, subtotales y totales |
| `normalize.py` | Nombres `snake_case` sin tildes, términos estandarizados (ore→mineral…), números (`1.234,5`, `(500)`, `US$`), fechas (`dd/mm/aaaa`, `Ene-12`, `Jan_2012`) |
| `columns.py` | Tipificación (fecha/entero/decimal/id/categoría), unificación de variantes y typos |
| `pii.py` | DNI, nombres, teléfonos, direcciones: excluir / anonimizar (SHA-256 salado). Fecha de nacimiento → rango de edad |
| `validate.py` | Rangos físicos (ley, tonelaje, horas, precipitación), atípicos, `Total YYYY` vs suma de meses |
| `reshape.py` | Columnas por mes → formato largo; tabla de hechos consolidada |
| `quality.py` | Puntaje 0-100 (completitud, validez, unicidad, uniformidad) antes/después |
| `export.py` | CSV, libro Excel (`Base_Reportes_Operativos_LaArena.xlsx`) y ZIP |

## Principios (alineados con las bases del reto)
1. **No se inventan datos.** Los vacíos siguen vacíos; los valores imposibles se anulan *y se registran*.
2. **Trazabilidad total.** Hojas `BITACORA` y `ANOMALIAS`: regla, columna, nº de celdas, ejemplo antes/después.
3. **Seguridad de datos personales.** La PII nunca llega a los CSV, al libro, a los ejemplos de la bitácora ni a las vistas.
4. **Lo dudoso se marca, no se corrige a ciegas** (atípicos, totales inconsistentes).

## Límites conocidos
- Solo CSV/Excel (los PDF del caso no se leen).
- Hojas con varias tablas apiladas de estructura distinta (p. ej. `Fuerza Laboral`) se procesan como una sola; revisar en el Explorador.
- Los encabezados de 3+ filas muy irregulares (p. ej. `WHITTLE_RESULTS`) pueden quedar con nombres compuestos poco legibles.
- Los términos técnicos y rangos de dominio están en `arena_core/config.py`: ajústalos con el criterio del equipo.

## Pruebas
`pytest tests` (datos sintéticos: título, `#REF!`, duplicados, DNI, total, ancho→largo, multiencabezado, latin-1).
