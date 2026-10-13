"""Ancho → largo (columnas por mes) y construcción de la tabla de hechos consolidada."""
from __future__ import annotations

import re

import pandas as pd

from .normalize import guess_unit

from .changelog import ChangeLog

PERIOD_COL = re.compile(r"^(\d{4})_(\d{2})(?:_(.+))?$")
FACT_COLS = ["fuente", "hoja", "fecha", "dimension", "indicador", "unidad", "valor"]


def to_long(df: pd.DataFrame, log: ChangeLog) -> pd.DataFrame | None:
    period_cols = [c for c in df.columns if PERIOD_COL.match(str(c)) and pd.api.types.is_numeric_dtype(df[c])]
    months = {PERIOD_COL.match(c).group(1) + PERIOD_COL.match(c).group(2) for c in period_cols}
    if len(period_cols) < 3 or len(months) < 2:
        return None
    total_cols = [c for c in df.columns if str(c).startswith("total") and c not in period_cols]
    id_cols = [c for c in df.columns if c not in period_cols and c not in total_cols]
    work = df[id_cols + period_cols].copy()
    work["_fila"] = range(len(work))
    m = work.melt(id_vars=id_cols + ["_fila"], value_vars=period_cols, var_name="_col", value_name="valor")
    parsed = m["_col"].map(lambda c: PERIOD_COL.match(c).groups())
    m["periodo"] = parsed.map(lambda g: pd.Timestamp(int(g[0]), int(g[1]), 1))
    m["_sufijo"] = parsed.map(lambda g: g[2])
    m = m.dropna(subset=["valor"])
    if m.empty:
        return None
    for c in id_cols:
        m[c] = m[c].astype(object).where(m[c].notna(), "(sin dato)")
    if m["_sufijo"].notna().any():
        m["_sufijo"] = m["_sufijo"].fillna("valor")
        keys = id_cols + ["_fila", "periodo"]
        out = m.groupby(keys + ["_sufijo"], dropna=False)["valor"].first().unstack("_sufijo").reset_index().drop(columns=["_fila"])
        out.columns.name = None
    else:
        out = m.drop(columns=["_col", "_sufijo", "_fila"])
        text_ids = [c for c in id_cols if not pd.api.types.is_numeric_dtype(df[c])]
        if len(text_ids) == 1 and len(id_cols) == 1:
            out = out.rename(columns={text_ids[0]: "indicador"})
        elif not id_cols:
            out.insert(0, "indicador", "(sin dato)")
    cols = [c for c in out.columns if c != "periodo"]
    # periodo después de los identificadores
    n_ids = len([c for c in cols if c in id_cols or c == "indicador"])
    cols.insert(n_ids, "periodo")
    out = out[cols].sort_values(["periodo"], kind="stable").reset_index(drop=True)
    log.add("ancho_a_largo", celdas=len(out), detalle=f"{len(period_cols)} columnas de periodo transformadas a formato largo ({len(out)} registros) para análisis y gráficos", severidad="info")
    return out


def facts_from(df: pd.DataFrame, source: str, sheet: str, units: dict[str, str]) -> pd.DataFrame | None:
    date_col = next((c for c in ("fecha", "periodo") if c in df.columns and "datetime" in str(df[c].dtype)), None)
    if date_col is None:
        return None
    num_cols = [c for c in df.columns if c != date_col and pd.api.types.is_numeric_dtype(df[c]) and "datetime" not in str(df[c].dtype)]
    dims = [c for c in df.columns if c != date_col and c not in num_cols and "datetime" not in str(df[c].dtype)]
    if not num_cols:
        return None
    d = df.copy()
    dimension = d[dims].astype(object).apply(lambda r: " | ".join(str(v) for v in r.tolist() if pd.notna(v)), axis=1) if dims else pd.Series("", index=d.index)
    if "valor" in num_cols and "indicador" in dims:
        dimension = d[[c for c in dims if c != "indicador"]].astype(object).apply(lambda r: " | ".join(str(v) for v in r.tolist() if pd.notna(v)), axis=1) if len(dims) > 1 else pd.Series("", index=d.index)
        out = pd.DataFrame({"fecha": d[date_col], "dimension": dimension, "indicador": d["indicador"], "valor": d["valor"]})
        uni = {k: (guess_unit(k) or ("t" if re.search(r"mineral|desmonte|toneladas|moved|process", str(k).lower()) else "")) for k in out["indicador"].dropna().unique()}
        out["unidad"] = out["indicador"].map(uni).fillna("")
    else:
        frames = []
        for c in num_cols:
            frames.append(pd.DataFrame({"fecha": d[date_col], "dimension": dimension, "indicador": c, "unidad": units.get(c) or guess_unit(c) or ("t" if c in ("mineral", "desmonte") else ""), "valor": d[c].astype(float)}))
        out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["valor"])
    out.insert(0, "hoja", sheet)
    out.insert(0, "fuente", source)
    return out[FACT_COLS].reset_index(drop=True)
