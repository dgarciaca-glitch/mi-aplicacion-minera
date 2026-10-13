"""Orquestador: tabla cruda → tabla corregida + bitácora + diccionario + calidad."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .changelog import ChangeLog
from .columns import clean_text_columns, convert_column, infer_type, unify_categories
from .config import CleaningConfig
from .ingest import RawTable
from .normalize import dedupe_names, guess_unit, norm_name
from .pii import apply_pii, detect_pii_columns
from .quality import quality_after, quality_before
from .reshape import facts_from, to_long
from .structure import blank_mask, drop_total_rows, extract_groups, extract_table, fill_hierarchy, split_blocks
from .validate import apply_domain_rules, check_totals, detect_outliers


@dataclass
class CleanResult:
    table_id: str
    source: str
    sheet: str
    titulo: str
    df: pd.DataFrame
    df_struct: pd.DataFrame
    long: pd.DataFrame | None
    facts: pd.DataFrame | None
    log: pd.DataFrame
    issues: pd.DataFrame
    dictionary: pd.DataFrame
    quality_before: dict
    quality_after: dict
    pii: dict = field(default_factory=dict)
    shape_in: tuple = (0, 0)
    shape_out: tuple = (0, 0)
    skipped: str = ""

    @property
    def n_changes(self) -> int:
        if self.log.empty:
            return 0
        return int(self.log.loc[~self.log["regla"].isin(["total_verificado"]), "celdas"].sum())


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    def rank(c):
        s = df[c]
        if "datetime" in str(s.dtype):
            return 0
        if pd.api.types.is_numeric_dtype(s):
            return 2
        return 1
    cols = sorted(df.columns, key=lambda c: (rank(c), list(df.columns).index(c)))
    return df[cols]


def _build_dictionary(df: pd.DataFrame, clean_names: list[str], originals: dict[str, str], pii: dict[str, str]) -> pd.DataFrame:
    rows = []
    for c in df.columns:
        s = df[c]
        is_dt = "datetime" in str(s.dtype)
        is_num = pd.api.types.is_numeric_dtype(s) and not is_dt
        orig = originals.get(c, "")
        row = {
            "columna": c, "nombre_original": orig,
            "tipo": "fecha" if is_dt else ("entero" if str(s.dtype) == "Int64" else ("decimal" if is_num else "texto")),
            "unidad": guess_unit(orig or c), "nulos": int(s.isna().sum()), "pct_nulos": round(100 * s.isna().mean(), 1) if len(s) else 0,
            "unicos": int(s.nunique()),
            "minimo": (s.min() if (is_num or is_dt) and s.notna().any() else ""),
            "maximo": (s.max() if (is_num or is_dt) and s.notna().any() else ""),
            "ejemplo": (str(s.dropna().iloc[0])[:60] if s.notna().any() else ""),
        }
        if is_dt:
            row["minimo"] = s.min().strftime("%Y-%m-%d") if s.notna().any() else ""
            row["maximo"] = s.max().strftime("%Y-%m-%d") if s.notna().any() else ""
        rows.append(row)
    return pd.DataFrame(rows)


def _redact(log: ChangeLog, struct: pd.DataFrame, pii: dict, clean: list[str]) -> None:
    """La PII no debe aparecer en ejemplos de la bitácora ni en la vista 'original estructurada'."""
    for rec in log.records:
        if rec["columna"] in pii:
            rec["antes"] = rec["despues"] = "[PII oculto]" if (rec["antes"] or rec["despues"]) else ""
    for iss in log.issues:
        if iss["columna"] in pii:
            iss["valor"] = "[PII oculto]"
    for name in pii:
        if name in clean:
            j = clean.index(name)
            if j < struct.shape[1]:
                struct.iloc[:, j] = "[PII oculto]"


def clean_block(raw_block: pd.DataFrame, table_id: str, source: str, sheet: str, cfg: CleaningConfig) -> CleanResult:
    log = ChangeLog(table_id)
    shape_in = raw_block.shape
    ext = extract_table(raw_block, cfg, log)
    df = ext.df
    df = fill_hierarchy(df, cfg, log)
    df = drop_total_rows(df, cfg, log)
    df = extract_groups(df, ext.names, log)

    # nombres de columna
    raw_names = list(ext.names)
    orig_names = list(ext.original_names)
    if "__grupo" in df.columns:
        raw_names.insert(0, "grupo")
        orig_names.insert(0, "(título de grupo)")
    clean = []
    for j, n in enumerate(raw_names):
        if n is None or (isinstance(n, float) and n != n):
            col = df.iloc[:, j]
            txt = col.map(lambda v: isinstance(v, str)).mean() if len(col) else 0
            clean.append("indicador" if (j == 0 and txt > 0.6) else f"col_{j + 1}")
        else:
            clean.append(norm_name(n, cfg.traducir_terminos))
    clean = dedupe_names(clean)
    originals = {c: (orig_names[j] if j < len(orig_names) else "") for j, c in enumerate(clean)}
    changed = [(o, c) for o, c in zip(raw_names, clean) if o is not None and str(o).strip() != c]
    if changed:
        log.add("columnas_renombradas", celdas=len(changed), antes=changed[0][0], despues=changed[0][1], detalle="Nombres de columna a snake_case sin tildes, términos técnicos estandarizados (ore→mineral, waste→desmonte…)")
    df.columns = clean

    # vista estructurada (antes de limpiar valores)
    struct = df.copy()
    struct.columns = dedupe_names([str(o) if o else c for o, c in zip(orig_names, clean)])
    q_before = quality_before(struct)

    df = clean_text_columns(df, log)
    nb = ~blank_mask(df)
    empty_rows = ~nb.any(axis=1) | ~df.notna().any(axis=1)
    if empty_rows.any():
        log.add("fila_vacia_retirada", celdas=int(empty_rows.sum()), detalle="Filas sin ningún dato")
        df = df.loc[~empty_rows]
    if cfg.quitar_columnas_vacias:
        null_ratio = df.isna().mean()
        drop_cols = [c for c in df.columns if null_ratio[c] >= cfg.umbral_columna_vacia]
        if drop_cols:
            log.add("columna_vacia_retirada", celdas=len(drop_cols), detalle="Columnas completamente vacías: " + ", ".join(drop_cols[:12]))
            df = df.drop(columns=drop_cols)
    if df.empty or df.shape[1] == 0:
        return CleanResult(table_id, source, sheet, ext.titulo, pd.DataFrame(), struct, None, None, log.frame(), log.issues_frame(),
                           pd.DataFrame(), q_before, q_before, {}, shape_in, (0, 0), "Sin datos tras limpiar")

    pii = detect_pii_columns(df, list(df.columns))

    # tipos
    types = {}
    for c in df.columns:
        t = infer_type(df[c], c, cfg)
        types[c] = t
        df[c] = convert_column(df[c], c, t, cfg, log)
        if t == "categoria" and cfg.unificar_categorias and c not in pii:
            df[c] = unify_categories(df[c], c, cfg, log)

    # duplicados exactos (antes de retirar PII)
    dup_rows = 0
    if cfg.quitar_duplicados and len(df) > 1:
        dup = df.astype(str).duplicated(keep="first") & (df.notna().mean(axis=1) >= 0.8)
        dup_rows = int(dup.sum())
        if dup_rows:
            first = df.index[dup][0]
            log.add("duplicado_retirado", celdas=dup_rows, fila=first, detalle=f"{dup_rows} filas idénticas a otra anterior fueron retiradas", severidad="aviso")
            df = df.loc[~dup]

    df = apply_pii(df, pii, cfg, log)
    if pii and cfg.pii_modo != "conservar":
        _redact(log, struct, pii, clean)
    df = apply_domain_rules(df, cfg, log)
    detect_outliers(df, cfg, log)
    check_totals(df, log)

    if cfg.ordenar_columnas:
        ordered = _order_columns(df)
        if list(ordered.columns) != list(df.columns):
            log.add("columnas_reordenadas", celdas=df.shape[1], detalle="Orden lógico: fechas → identificadores/categorías → medidas numéricas")
        df = ordered
    first_dt = next((c for c in df.columns if "datetime" in str(df[c].dtype)), None)
    if first_dt and df.columns[0] == first_dt and not df[first_dt].dropna().is_monotonic_increasing:
        df = df.sort_values(first_dt, kind="stable")
        log.add("ordenado_por_fecha", first_dt, len(df), detalle="Filas ordenadas cronológicamente")

    issues = log.issues_frame()
    flagged = len(issues) if not issues.empty else 0
    log_df = log.frame()
    flagged_cells = int(log_df.loc[log_df["regla"].isin(["regla_dominio", "total_inconsistente", "fecha_fuera_de_rango"]), "celdas"].sum()) if not log_df.empty else 0
    out = df.reset_index(drop=True)
    q_after = quality_after(out, flagged_cells, 0)
    dictionary = _build_dictionary(out, list(out.columns), originals, pii)
    units = {r["columna"]: r["unidad"] for _, r in dictionary.iterrows()}
    long = to_long(out, log) if cfg.ancho_a_largo else None
    base = long if long is not None else out
    facts = facts_from(base, source, sheet, units)
    log_df = log.frame()
    return CleanResult(table_id, source, sheet, ext.titulo, out, struct.reset_index(drop=True), long, facts, log_df, issues, dictionary,
                       q_before, q_after, pii, shape_in, out.shape)


def clean_table(raw: RawTable, cfg: CleaningConfig | None = None) -> list[CleanResult]:
    cfg = cfg or CleaningConfig()
    scratch = ChangeLog(raw.table_id)
    blocks = split_blocks(raw.df, cfg, scratch)
    results = []
    if not blocks:
        return [CleanResult(raw.table_id, raw.source, raw.sheet, "", pd.DataFrame(), pd.DataFrame(), None, None, scratch.frame(), scratch.issues_frame(),
                            pd.DataFrame(), {}, {}, {}, raw.df.shape, (0, 0), "Hoja sin datos")]
    for b in blocks:
        tid = raw.table_id + (f"#{b.label}" if b.label else "")
        res = clean_block(b.df, tid, raw.source, raw.sheet, cfg)
        if not scratch.records == [] and b is blocks[0]:
            extra = ChangeLog(tid, records=[{**r, "tabla": tid} for r in scratch.records])
            res.log = pd.concat([extra.frame(), res.log], ignore_index=True)
        results.append(res)
    return results


def clean_all(tables: list[RawTable], cfg: CleaningConfig | None = None, progress=None) -> list[CleanResult]:
    cfg = cfg or CleaningConfig()
    out: list[CleanResult] = []
    for i, t in enumerate(tables):
        try:
            out += clean_table(t, cfg)
        except Exception as exc:  # noqa: BLE001
            out.append(CleanResult(t.table_id, t.source, t.sheet, "", pd.DataFrame(), pd.DataFrame(), None, None, pd.DataFrame(), pd.DataFrame(),
                                   pd.DataFrame(), {}, {}, {}, t.df.shape, (0, 0), f"Error al procesar: {type(exc).__name__}: {exc}"))
        if progress:
            progress((i + 1) / len(tables), t.table_id)
    return out


def consolidate_facts(results: list[CleanResult]) -> pd.DataFrame:
    frames = [r.facts for r in results if r.facts is not None and not r.facts.empty]
    if not frames:
        return pd.DataFrame(columns=["fuente", "hoja", "fecha", "dimension", "indicador", "unidad", "valor"])
    return pd.concat(frames, ignore_index=True).sort_values(["fecha", "fuente", "hoja"], kind="stable").reset_index(drop=True)
