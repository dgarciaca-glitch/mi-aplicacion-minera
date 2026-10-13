"""Analizador de datos de La Arena — interfaz Streamlit.

Ejecutar:  streamlit run app.py
Motor:     arena_core (el mismo que usa la CLI y las pruebas).
"""
from __future__ import annotations

import io
import json
import time
import warnings

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from arena_core import CleaningConfig, clean_all, consolidate_facts, load_folder, load_inputs
from arena_core.export import (
    all_dictionary, all_issues, all_logs, build_workbook, build_zip, quality_frame, slug, summary_frame, to_csv_bytes,
)

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------- compatibilidad de versiones
_VER = tuple(int(x) for x in st.__version__.split(".")[:2])
STRETCH = {"width": "stretch"} if _VER >= (1, 50) else {"use_container_width": True}

st.set_page_config(page_title="Analizador de datos de La Arena", page_icon="⛏️", layout="wide", initial_sidebar_state="expanded")

CSS = """
<style>
:root { --verde:#1F3A2E; --oro:#C8A24A; }
.block-container { padding-top: 1.4rem; max-width: 1500px; }
.hero { border:1px solid rgba(128,128,128,.25); border-left:6px solid var(--oro); border-radius:10px; padding:14px 20px; margin-bottom:14px; }
.hero h1 { margin:0; font-size:1.7rem; }
.hero p { margin:2px 0 0 0; opacity:.75; font-size:.92rem; }
.chip { display:inline-block; padding:2px 10px; margin:2px 4px 2px 0; border-radius:999px; font-size:.78rem; border:1px solid rgba(128,128,128,.35); }
.chip.ok { border-color:#2e9e5b; color:#2e9e5b; } .chip.warn { border-color:#d99a00; color:#d99a00; } .chip.bad { border-color:#d64545; color:#d64545; }
div[data-testid="stMetric"] { border:1px solid rgba(128,128,128,.25); border-radius:10px; padding:10px 14px; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------- utilidades
def _sev_chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{text}</span>'


def _fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", " ")


@st.cache_data(show_spinner=False, max_entries=4)
def _process(items: tuple, folder: str, cfg_json: str):
    cfg = CleaningConfig(**json.loads(cfg_json))
    tables, warns = [], []
    if items:
        t, w = load_inputs([(n, d) for n, d in items])
        tables += t
        warns += w
    if folder.strip():
        t, w = load_folder(folder)
        tables += t
        warns += w
    return tables, warns, cfg


def run_pipeline(items, folder, cfg: CleaningConfig):
    tables, warns, cfg = _process(items, folder, json.dumps(cfg.to_dict()))
    bar = st.progress(0.0, text="Procesando…")
    t0 = time.time()

    def cb(frac, name):
        bar.progress(min(frac, 1.0), text=f"Limpiando {name}")

    results = clean_all(tables, cfg, progress=cb)
    bar.empty()
    return results, warns, time.time() - t0


# ----------------------------------------------------------------- barra lateral
with st.sidebar:
    st.subheader("Datos de entrada")
    uploads = st.file_uploader("Importar CSV o Excel", type=["csv", "tsv", "txt", "xlsx", "xlsm", "xls", "zip"], accept_multiple_files=True)
    folder = st.text_input("O una carpeta de tu equipo", placeholder=r"C:\Users\USER\Downloads\archivos", help="Se leen todos los CSV/Excel/ZIP de la carpeta, incluidas subcarpetas.")
    st.caption("Los datos personales (DNI, nombres) se excluyen automáticamente.")

    with st.expander("⚙️ Reglas de limpieza", expanded=False):
        pii_modo = st.radio("Datos personales", ["excluir", "anonimizar", "conservar"], horizontal=True,
                            help="excluir: se elimina la columna · anonimizar: hash SHA-256 salado (permite cruzar tablas) · conservar: no recomendado")
        c1, c2 = st.columns(2)
        quitar_dup = c1.checkbox("Quitar duplicados", True)
        quitar_tot = c2.checkbox("Quitar totales/subtotales", True)
        unif = c1.checkbox("Unificar categorías", True)
        anular = c2.checkbox("Anular imposibles", True, help="Valores fuera de rango físico (ley negativa, tonelaje negativo…) pasan a vacío y se registran.")
        largo = c1.checkbox("Ancho → largo", True, help="Columnas Jan_2012, Feb_2012… pasan a filas (periodo, valor).")
        tradu = c2.checkbox("Estandarizar términos", True, help="ore→mineral, waste→desmonte, hrs→horas…")
        atip = c1.checkbox("Detectar atípicos", True)
        dividir = c2.checkbox("Separar bloques", True, help="Varias tablas en una hoja → una tabla por bloque.")
        fuzzy = st.slider("Similitud para typos en categorías", 0.80, 1.00, 0.93, 0.01, help="1.00 desactiva la corrección de errores de digitación.")
        z = st.slider("Umbral de atípicos (z robusto)", 4.0, 15.0, 8.0, 0.5)
        sep = st.selectbox("Separador del CSV de salida", [",", ";", "\t"], format_func=lambda s: {",": "coma ( , )", ";": "punto y coma ( ; ) — Excel en español", "\t": "tabulador"}[s])

    go_btn = st.button("▶ Procesar", type="primary", **STRETCH)
    st.caption("Principio: **no se inventan datos**. Todo cambio queda en la bitácora.")

cfg = CleaningConfig(
    pii_modo=pii_modo, quitar_duplicados=quitar_dup, quitar_totales=quitar_tot, unificar_categorias=unif,
    anular_imposibles=anular, ancho_a_largo=largo, traducir_terminos=tradu, detectar_atipicos=atip, dividir_bloques=dividir,
    fuzzy_corte=fuzzy, z_atipico=z,
)

st.markdown(
    '<div class="hero"><h1>Analizador de datos de La Arena</h1>'
    "<p>Importa CSV o Excel (o una carpeta) → el sistema detecta la estructura, corrige, ordena, valida y entrega CSV limpios, "
    "una base consolidada y un libro listo para Google Sheets.</p></div>",
    unsafe_allow_html=True,
)

items = tuple((f.name, f.getvalue()) for f in (uploads or []))
if go_btn or (items or folder.strip()) and "results" not in st.session_state:
    if not items and not folder.strip():
        st.warning("Sube al menos un archivo o escribe la ruta de una carpeta.")
    else:
        with st.spinner("Leyendo archivos…"):
            results, warns, secs = run_pipeline(items, folder, cfg)
        st.session_state.update(results=results, warns=warns, secs=secs, sep=sep, cfg=cfg)
        for k_ in ("pack_xlsx", "pack_zip"):
            st.session_state.pop(k_, None)

if "results" not in st.session_state:
    st.info("Importa uno o varios CSV o Excel en la barra lateral, o escribe la ruta de la carpeta con tus archivos. El análisis aparece aquí en cuanto haya datos.")
    st.stop()

results = st.session_state["results"]
warns = st.session_state["warns"]
out_sep = sep
ok = [r for r in results if not r.df.empty]
facts = consolidate_facts(results)
summ = summary_frame(results)
logs = all_logs(results)
issues = all_issues(results)

for w in warns:
    st.warning(w, icon="⚠️")
failed = [r for r in results if r.skipped and r.df.empty]

# ----------------------------------------------------------------- KPIs
rows_in = sum(r.shape_in[0] for r in results)
rows_out = sum(r.shape_out[0] for r in results)
q_b = pd.Series([r.quality_before.get("puntaje") for r in ok]).mean()
q_a = pd.Series([r.quality_after.get("puntaje") for r in ok]).mean()
pii_cols = sum(len(r.pii) for r in results)
k = st.columns(6)
k[0].metric("Tablas procesadas", f"{len(ok)} / {len(results)}")
k[1].metric("Filas entrada → salida", f"{_fmt_int(rows_in)} → {_fmt_int(rows_out)}")
k[2].metric("Celdas corregidas", _fmt_int(sum(r.n_changes for r in results)))
k[3].metric("Calidad media", f"{q_a:.1f}", f"{q_a - q_b:+.1f} vs. entrada")
k[4].metric("Registros en base consolidada", _fmt_int(len(facts)))
k[5].metric("Columnas con PII tratadas", pii_cols)

tabs = st.tabs(["📋 Resumen", "🔎 Explorador", "🧾 Bitácora", "🚨 Anomalías", "📈 Calidad", "📊 Visual", "🗂️ Consolidado", "📦 Exportar"])

# ----------------------------------------------------------------- Resumen
with tabs[0]:
    st.caption(f"Procesado en {st.session_state['secs']:.1f} s · {len(failed)} tabla(s) sin datos utilizables")
    view = summ.copy()
    view["estado"] = view["estado"].map(lambda s: "OK" if s == "OK" else s)
    st.dataframe(
        view,
        column_config={
            "calidad_antes": st.column_config.ProgressColumn("calidad antes", min_value=0, max_value=100, format="%.0f"),
            "calidad_despues": st.column_config.ProgressColumn("calidad después", min_value=0, max_value=100, format="%.0f"),
        },
        hide_index=True, height=480, **STRETCH,
    )
    if failed:
        with st.expander(f"Tablas sin datos ({len(failed)})"):
            st.dataframe(pd.DataFrame({"tabla": [r.table_id for r in failed], "motivo": [r.skipped for r in failed]}), hide_index=True, **STRETCH)

# ----------------------------------------------------------------- Explorador
with tabs[1]:
    ids = [r.table_id for r in ok]
    sel = st.selectbox("Tabla", ids, key="sel_tabla")
    r = next(x for x in ok if x.table_id == sel)
    if r.titulo:
        st.caption(f"Título detectado en la hoja: **{r.titulo}**")
    a, b = st.columns([3, 2])
    chips = [_sev_chip(f"{r.shape_in[0]}×{r.shape_in[1]} → {r.shape_out[0]}×{r.shape_out[1]}"), _sev_chip(f"calidad {r.quality_before['puntaje']} → {r.quality_after['puntaje']}", "ok")]
    if r.pii:
        chips.append(_sev_chip(f"PII: {', '.join(r.pii)}", "warn"))
    if r.long is not None:
        chips.append(_sev_chip(f"formato largo disponible ({len(r.long)} registros)", "ok"))
    a.markdown("".join(chips), unsafe_allow_html=True)
    modo = b.radio("Vista", ["Corregida", "Original estructurada", "Formato largo"], horizontal=True, label_visibility="collapsed")
    if modo == "Corregida":
        st.caption("Puedes editar celdas; la descarga usa tu versión editada.")
        edited = st.data_editor(r.df, num_rows="dynamic", key=f"ed_{sel}", height=430, **STRETCH)
        st.session_state[f"edited_{sel}"] = edited
        st.download_button("⬇ CSV corregido de esta tabla", to_csv_bytes(edited, out_sep), file_name=f"{slug(sel)}.csv", mime="text/csv")
    elif modo == "Original estructurada":
        st.dataframe(r.df_struct.astype(str), height=430, **STRETCH)
        st.caption("Datos tal como venían, ya con encabezado detectado, sin ninguna corrección de valores.")
    else:
        if r.long is None:
            st.info("Esta tabla no tiene columnas por periodo.")
        else:
            st.dataframe(r.long, height=430, **STRETCH)
    st.markdown("**Diccionario de datos**")
    st.dataframe(r.dictionary, hide_index=True, height=260, **STRETCH)

# ----------------------------------------------------------------- Bitácora
with tabs[2]:
    if logs.empty:
        st.success("No hubo correcciones.")
    else:
        f1, f2, f3 = st.columns(3)
        reglas = f1.multiselect("Regla", sorted(logs["regla"].unique()))
        tablas = f2.multiselect("Tabla", sorted(logs["tabla"].unique()))
        sevs = f3.multiselect("Severidad", sorted(logs["severidad"].unique()))
        v = logs
        if reglas:
            v = v[v["regla"].isin(reglas)]
        if tablas:
            v = v[v["tabla"].isin(tablas)]
        if sevs:
            v = v[v["severidad"].isin(sevs)]
        by_rule = logs.groupby("regla")["celdas"].sum().sort_values().reset_index()
        fig = px.bar(by_rule, x="celdas", y="regla", orientation="h", title="Celdas/filas afectadas por regla")
        fig.update_layout(height=max(300, 22 * len(by_rule)), margin=dict(l=0, r=0, t=40, b=0))
        c1, c2 = st.columns([2, 3])
        c1.plotly_chart(fig, **STRETCH)
        c2.dataframe(v, hide_index=True, height=420, **STRETCH)
        st.download_button("⬇ Bitácora completa (CSV)", to_csv_bytes(logs, out_sep), file_name="bitacora_correcciones.csv", mime="text/csv")

# ----------------------------------------------------------------- Anomalías
with tabs[3]:
    if issues.empty:
        st.success("Sin anomalías puntuales.")
    else:
        st.caption("Celdas concretas con problema. 'anulado' = pasó a vacío; 'marcado' = se conserva pero requiere revisión humana.")
        a1, a2 = st.columns(2)
        reg = a1.multiselect("Regla ", sorted(issues["regla"].unique()))
        acc = a2.multiselect("Acción", sorted(issues["accion"].unique()))
        v = issues
        if reg:
            v = v[v["regla"].isin(reg)]
        if acc:
            v = v[v["accion"].isin(acc)]
        st.dataframe(v, hide_index=True, height=460, **STRETCH)
        st.download_button("⬇ Anomalías (CSV)", to_csv_bytes(issues, out_sep), file_name="anomalias.csv", mime="text/csv")

# ----------------------------------------------------------------- Calidad
with tabs[4]:
    q = quality_frame(results)
    if q.empty:
        st.info("Sin datos de calidad.")
    else:
        dims = ["completitud", "validez", "unicidad", "uniformidad"]
        agg = q.groupby("fase")[dims].mean().reindex(["antes", "despues"])
        fig = go.Figure()
        for fase, color in (("antes", "#d64545"), ("despues", "#2e9e5b")):
            fig.add_trace(go.Scatterpolar(r=(agg.loc[fase] * 100).tolist() + [agg.loc[fase].iloc[0] * 100], theta=dims + [dims[0]], name=fase, line_color=color, fill="toself", opacity=0.55))
        fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100])), height=420, title="Dimensiones de calidad (promedio de tablas)")
        c1, c2 = st.columns([2, 3])
        c1.plotly_chart(fig, **STRETCH)
        piv = q.pivot(index="tabla", columns="fase", values="puntaje").dropna()
        piv["mejora"] = piv["despues"] - piv["antes"]
        c2.markdown("**Tablas con mayor mejora**")
        c2.dataframe(piv.sort_values("mejora", ascending=False).head(15), height=380, **STRETCH)
        st.caption("Completitud = celdas con dato válido · Validez = valores que cumplen tipo y rango · Unicidad = sin filas repetidas · Uniformidad = un solo formato por columna.")

# ----------------------------------------------------------------- Visual
with tabs[5]:
    cand = [r for r in ok if any("datetime" in str(r.df[c].dtype) for c in r.df.columns) or r.long is not None]
    if not facts.empty:
        st.markdown("**Series de tiempo de la base consolidada**")
        inds = facts.groupby("indicador")["valor"].count().sort_values(ascending=False)
        pick = st.multiselect("Indicadores", inds.index.tolist(), default=inds.index.tolist()[:2])
        if pick:
            f = facts[facts["indicador"].isin(pick)]
            g = f.groupby(["fecha", "indicador"], as_index=False)["valor"].sum()
            st.plotly_chart(px.line(g, x="fecha", y="valor", color="indicador", markers=True, height=420), **STRETCH)
    st.markdown("**Distribución / correlación de una tabla**")
    sel_v = st.selectbox("Tabla ", [r.table_id for r in ok], key="vis_tabla")
    rv = next(x for x in ok if x.table_id == sel_v)
    nums = [c for c in rv.df.columns if pd.api.types.is_numeric_dtype(rv.df[c]) and "datetime" not in str(rv.df[c].dtype)]
    if nums:
        v1, v2 = st.columns(2)
        col = v1.selectbox("Columna numérica", nums)
        v1.plotly_chart(px.histogram(rv.df, x=col, nbins=40, height=340), **STRETCH)
        if len(nums) >= 3:
            corr = rv.df[nums[:25]].astype(float).corr()
            v2.plotly_chart(px.imshow(corr, color_continuous_scale="RdBu", zmin=-1, zmax=1, height=340, aspect="auto"), **STRETCH)
    else:
        st.info("Esta tabla no tiene columnas numéricas.")

# ----------------------------------------------------------------- Consolidado
with tabs[6]:
    st.caption("Base única en formato largo: una fila por (fecha, dimensión, indicador). Es la pestaña HECHOS_CONSOLIDADO del libro.")
    if facts.empty:
        st.info("Ninguna tabla tenía una columna de fecha o periodo + valores numéricos.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Registros", _fmt_int(len(facts)))
        m2.metric("Indicadores", facts["indicador"].nunique())
        m3.metric("Rango de fechas", f"{facts['fecha'].min():%Y-%m} → {facts['fecha'].max():%Y-%m}")
        st.dataframe(facts, hide_index=True, height=420, **STRETCH)

# ----------------------------------------------------------------- Exportar
with tabs[7]:
    st.markdown("### Entregables")
    st.caption("Se generan bajo demanda (el libro con todas las hojas tarda unos segundos).")
    if st.button("⚙️ Preparar entregables", type="primary"):
        with st.spinner("Armando libro Excel y ZIP…"):
            st.session_state["pack_xlsx"] = build_workbook(results)
            st.session_state["pack_zip"] = build_zip(results, out_sep)
    e1, e2 = st.columns(2)
    with e1:
        st.markdown("**① Libro para Google Sheets**")
        st.caption("Incluye LEEME, ÍNDICE, CALIDAD, HECHOS_CONSOLIDADO, BITÁCORA, ANOMALÍAS, DICCIONARIO y una hoja por tabla limpia.")
        if "pack_xlsx" in st.session_state:
            st.download_button("⬇ Base_Reportes_Operativos_LaArena.xlsx", st.session_state["pack_xlsx"], file_name="Base_Reportes_Operativos_LaArena.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.caption("En Google Drive: Nuevo → Subir archivo → abrir con Hojas de cálculo.")
    with e2:
        st.markdown("**② ZIP completo**")
        st.caption("CSV corregido por tabla, formato largo, consolidado, reportes y el libro.")
        if "pack_zip" in st.session_state:
            st.download_button("⬇ entregables_la_arena.zip", st.session_state["pack_zip"], file_name="entregables_la_arena.zip", mime="application/zip")
    st.divider()
    st.markdown("**Reporte técnico (JSON)** — configuración usada y métricas, útil para el registro de uso de IA y la defensa.")
    report = {
        "generado": time.strftime("%Y-%m-%d %H:%M:%S"), "config": st.session_state["cfg"].to_dict(),
        "tablas": len(results), "filas_entrada": int(rows_in), "filas_salida": int(rows_out),
        "calidad_media_antes": round(float(q_b), 1), "calidad_media_despues": round(float(q_a), 1),
        "registros_consolidados": int(len(facts)), "anomalias": int(len(issues)),
        "reglas_aplicadas": logs.groupby("regla")["celdas"].sum().to_dict() if not logs.empty else {},
    }
    st.code(json.dumps(report, indent=2, ensure_ascii=False), language="json")
    st.download_button("⬇ reporte.json", json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8"), file_name="reporte.json", mime="application/json")
