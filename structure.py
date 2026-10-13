"""Detección de estructura: bloques, encabezados (1..n filas), jerarquías, subtotales."""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .changelog import ChangeLog
from .config import CleaningConfig
from .normalize import clean_text_value, parse_number, strip_accents


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:
        return True
    if v is pd.NaT:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def blank_mask(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda c: c.map(is_blank))


def _is_stringy(v) -> bool:
    """Texto que NO es número ni fecha (sirve para reconocer encabezados)."""
    if isinstance(v, (_dt.datetime, _dt.date, pd.Timestamp)):
        return False
    if not isinstance(v, str):
        return False
    return parse_number(v) is None


@dataclass
class Block:
    df: pd.DataFrame
    label: str = ""


@dataclass
class Extracted:
    df: pd.DataFrame                 # datos crudos con columnas = nombres de encabezado (aún sin normalizar)
    titulo: str = ""
    header_rows: list = field(default_factory=list)
    original_names: list = field(default_factory=list)
    names: list = field(default_factory=list)
    has_header: bool = True


# ------------------------------------------------------------------ bloques
def trim_empty(df: pd.DataFrame) -> pd.DataFrame:
    nb = ~blank_mask(df)
    return df.loc[nb.any(axis=1), nb.any(axis=0)]


def _runs(flags: list[bool], gap: int) -> list[tuple[int, int]]:
    """Segmentos consecutivos de True separados por ≥gap False."""
    segs, start, last, blanks = [], None, None, 0
    for i, f in enumerate(flags):
        if f:
            if start is None:
                start = i
            last, blanks = i, 0
        else:
            if start is not None:
                blanks += 1
                if blanks >= gap:
                    segs.append((start, last))
                    start, blanks = None, 0
    if start is not None:
        segs.append((start, last))
    return segs


def split_blocks(df: pd.DataFrame, cfg: CleaningConfig, log: ChangeLog) -> list[Block]:
    df = trim_empty(df)
    if df.empty:
        return []
    if not cfg.dividir_bloques:
        return [Block(df)]
    nb = ~blank_mask(df)
    row_segs = _runs(nb.any(axis=1).tolist(), gap=2)
    blocks: list[Block] = []
    for rs, re_ in row_segs:
        seg = df.iloc[rs : re_ + 1]
        segnb = nb.iloc[rs : re_ + 1]
        col_groups = _runs(segnb.any(axis=0).tolist(), gap=1)
        big = [g for g in col_groups if (g[1] - g[0] + 1) >= 3 and segnb.iloc[:, g[0] : g[1] + 1].any(axis=1).sum() >= cfg.min_filas_bloque]
        if len(big) >= 2:
            for k, (c0, c1) in enumerate(big, 1):
                blocks.append(Block(trim_empty(seg.iloc[:, c0 : c1 + 1]), f"b{len(blocks)+1}"))
            log.add("bloques_separados", celdas=len(big), detalle=f"Se separaron {len(big)} tablas lado a lado en la hoja")
        else:
            blocks.append(Block(trim_empty(seg), f"b{len(blocks)+1}"))
    if len(blocks) > 1:
        keep = [b for b in blocks if len(b.df) >= cfg.min_filas_bloque and b.df.shape[1] >= 2]
        dropped = [b for b in blocks if b not in keep]
        for b in dropped:
            txt = " | ".join(str(v) for v in b.df.stack().head(3).tolist())
            log.add("bloque_descartado", celdas=int(b.df.size), detalle=f"Bloque pequeño tratado como nota/título: {txt}", severidad="aviso")
        blocks = keep or blocks[:1]
        for i, b in enumerate(blocks, 1):
            b.label = f"t{i}" if len(blocks) > 1 else ""
    else:
        blocks[0].label = ""
    return blocks


# --------------------------------------------------------------- encabezados
def _row_info(row: pd.Series):
    vals = [v for v in row.tolist() if not is_blank(v)]
    n = len(vals)
    s = sum(1 for v in vals if _is_stringy(v))
    num = sum(1 for v in vals if parse_number(v) is not None and not isinstance(v, str)) + sum(
        1 for v in vals if isinstance(v, str) and parse_number(v) is not None
    )
    return n, s, num


def find_header_row(df: pd.DataFrame, scan: int = 30) -> int | None:
    sub = df.iloc[:scan]
    infos = [_row_info(sub.iloc[i]) for i in range(len(sub))]
    # el ancho de referencia sale solo de filas "de texto" (las filas de datos numéricos no cuentan)
    maxn = max((n for n, s, _ in infos if n and s / n >= 0.7), default=0)
    if maxn < 2:
        return None
    for i, (n, s, _num) in enumerate(infos):
        if n >= max(2, 0.45 * maxn) and s / n >= 0.7:
            return i
    return None


def _fmt_part(v) -> str:
    if isinstance(v, (_dt.datetime, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, float) and v == int(v):
        v = int(v)
    if isinstance(v, int) and 1 <= v <= 31:
        return f"{v:02d}"
    s = str(v).strip()
    if re.fullmatch(r"\d{1,2}", s) and 1 <= int(s) <= 31:
        return f"{int(s):02d}"
    return s


def build_header(block: pd.DataFrame, h: int) -> tuple[list[str], int, list[str]]:
    """Devuelve (nombres, posición de primera fila de datos, nombres originales legibles)."""
    ncols = block.shape[1]
    rows = [block.iloc[h]]
    k = h + 1
    # apila filas de encabezado adicionales
    while k < len(block) and len(rows) < 4:
        cur, nxt = rows[-1], block.iloc[k]
        cur_nb = [not is_blank(v) for v in cur.tolist()]
        first, last = (cur_nb.index(True), len(cur_nb) - 1 - cur_nb[::-1].index(True)) if any(cur_nb) else (0, -1)
        gaps = [i for i in range(first, last + 1) if not cur_nb[i]]
        nxt_nb = [not is_blank(v) for v in nxt.tolist()]
        n_nxt = sum(nxt_nb)
        span_rule = len(gaps) >= 2 and n_nxt > 0 and sum(nxt_nb[i] for i in gaps) / len(gaps) >= 0.5
        n2, s2, _ = _row_info(nxt)
        n_cur = sum(cur_nb)
        after = block.iloc[k + 1] if k + 1 < len(block) else None
        num_after = (_row_info(after)[2] / max(_row_info(after)[0], 1) >= 0.5) if after is not None else False
        stack_rule = n2 >= 0.5 * n_cur and n2 > 0 and s2 / max(n2, 1) >= 0.8 and num_after
        if span_rule or stack_rule:
            rows.append(nxt)
            k += 1
        else:
            break
    # construye nombres con propagación horizontal en tramos
    levels: list[list] = []
    spanned: list[list[bool]] = []
    for ridx, r in enumerate(rows):
        vals = [None if is_blank(v) else v for v in r.tolist()]
        sp = [False] * ncols
        if ridx < len(rows) - 1:
            nxt = rows[ridx + 1]
            nxt_nb = [not is_blank(v) for v in nxt.tolist()]
            filled, cur, start = list(vals), None, None
            for i, v in enumerate(vals):
                if v is not None:
                    cur, start = v, i
                elif cur is not None and nxt_nb[i]:
                    filled[i] = cur
                    sp[i] = True
                    sp[start] = True
            vals = filled
        levels.append(vals)
        spanned.append(sp)
    names, originals = [], []
    for j in range(ncols):
        parts = []
        for li, vals in enumerate(levels):
            v = vals[j]
            if v is None:
                continue
            if li > 0 and not any(spanned[x][j] for x in range(li)):
                continue  # subnivel sin tramo: se ignora (ej. etiqueta 'DIA')
            parts.append(_fmt_part(v) if li > 0 else (str(v).strip() if not isinstance(v, (_dt.datetime, pd.Timestamp)) else v))
        if not parts:
            names.append(None)
            originals.append("")
        else:
            if len(parts) == 1:
                names.append(parts[0])
            else:
                names.append("_".join(str(p) if not isinstance(p, pd.Timestamp) else p.strftime("%b_%Y") for p in parts))
            originals.append(" / ".join(str(p) for p in parts))
    return names, k, originals


# --------------------------------------------------------------- jerarquías
_TOTAL_LABEL = re.compile(
    r"^(grand\s+)?(sub\s*)?totals?(\s+general(es)?)?\s*[:.]*$|^suma(\s+total)?\s*[:.]*$|^total\s+(general|empresa|cia\.?)\s*[:.]*$|^resumen\s+total$"
)


def _label_norm(v) -> str:
    return re.sub(r"\s+", " ", strip_accents(str(v)).lower()).strip()


def fill_hierarchy(df: pd.DataFrame, cfg: CleaningConfig, log: ChangeLog) -> pd.DataFrame:
    """Columnas de etiqueta escalonadas a la izquierda (tablas dinámicas exportadas): rellena y retira subtotales."""
    if len(df) < 4 or df.shape[1] < 3:
        return df
    nb = ~blank_mask(df)
    blank_ratio = 1 - nb.mean()
    dense = [j for j in range(df.shape[1]) if blank_ratio.iloc[j] < 0.35]
    if not dense:
        return df
    first_dense = dense[0]
    lead = []
    for j in range(first_dense):
        col = df.iloc[:, j]
        vals = [v for v in col.tolist() if not is_blank(v)]
        if len(vals) < 2 or blank_ratio.iloc[j] <= 0.35:
            continue
        floats = [v for v in vals[:50] if parse_number(v) is not None and not float(parse_number(v)).is_integer()]
        if not floats:
            lead.append(j)
    if not lead:
        return df
    rest = [j for j in range(df.shape[1]) if j not in lead]
    # patrón escalonado/grupo: en las filas con valores, ≤1 etiqueta líder o columna líder con vacíos estructurales
    lead_nb = nb.iloc[:, lead]
    if not (lead_nb.sum(axis=1) < len(lead)).mean() > 0.4:
        return df
    level = lead_nb.apply(lambda r: max([i for i, f in enumerate(r.tolist()) if f], default=-1), axis=1)
    data_mask = nb.iloc[:, rest].any(axis=1)
    out = df.copy()
    # relleno hacia abajo con reinicio de niveles inferiores
    cur: dict[int, object] = {}
    for pos in range(len(out)):
        for li, j in enumerate(lead):
            v = df.iat[pos, j]
            if not is_blank(v):
                cur[li] = v
                for deeper in [x for x in cur if x > li]:
                    del cur[deeper]
            elif li in cur and data_mask.iat[pos]:
                out.iat[pos, j] = cur[li]
    filled = int((blank_mask(out.iloc[:, lead]) != blank_mask(df.iloc[:, lead])).sum().sum())
    if filled:
        log.add("jerarquia_rellenada", celdas=filled, detalle=f"Etiquetas de grupo propagadas en {len(lead)} columna(s) de jerarquía")
    drop = pd.Series(False, index=out.index)
    sec = ~data_mask
    drop |= sec
    n_sec = int(sec.sum())
    if n_sec:
        log.add("fila_seccion_retirada", celdas=n_sec, detalle="Filas que solo contenían un título de grupo (su etiqueta pasó a las filas hijas)")
    if cfg.quitar_totales:
        lv = level.tolist()
        sub = [bool(data_mask.iat[i]) and lv[i] >= 0 and i + 1 < len(lv) and lv[i + 1] > lv[i] for i in range(len(lv))]
        sub = pd.Series(sub, index=out.index)
        if sub.any():
            log.add("subtotal_retirado", celdas=int(sub.sum()), detalle="Filas de subtotal de tabla dinámica retiradas para evitar doble conteo", severidad="aviso")
        drop |= sub
    return out.loc[~drop]


def drop_total_rows(df: pd.DataFrame, cfg: CleaningConfig, log: ChangeLog) -> pd.DataFrame:
    if not cfg.quitar_totales or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for j in range(min(df.shape[1], 4)):
        mask |= df.iloc[:, j].map(lambda v: isinstance(v, str) and bool(_TOTAL_LABEL.match(_label_norm(v))))
    if mask.any():
        for idx in df.index[mask][:3]:
            log.add("fila_total_retirada", fila=idx, detalle="Fila de total declarada retirada (el total se recalcula, no se arrastra)", severidad="aviso")
        if mask.sum() > 3:
            log.add("fila_total_retirada", celdas=int(mask.sum()) - 3, detalle="…y más filas de total")
    return df.loc[~mask]


def extract_table(block: pd.DataFrame, cfg: CleaningConfig, log: ChangeLog) -> Extracted:
    h = find_header_row(block)
    titulo = ""
    if h is None:
        df = block.copy()
        df.columns = [None] * df.shape[1]
        return Extracted(df=df, titulo="", has_header=False, original_names=[""] * df.shape[1], names=[None] * df.shape[1])
    if h > 0:
        t = [str(v).strip() for v in block.iloc[:h].stack().tolist() if not is_blank(v)]
        titulo = " · ".join(dict.fromkeys(t))
        log.add("titulo_separado", celdas=h, detalle=f"{h} fila(s) de título/membrete sobre el encabezado: {titulo[:120]}")
    names, first_data, originals = build_header(block, h)
    n_hdr = first_data - h
    if n_hdr > 1:
        log.add("encabezado_multinivel", celdas=n_hdr, detalle=f"Encabezado de {n_hdr} filas combinado en un solo nombre por columna")
    df = block.iloc[first_data:].copy()
    df.columns = list(range(df.shape[1]))
    # filas que repiten el encabezado
    hdr_txt = [_label_norm(n) for n in originals]
    rep = df.apply(lambda r: sum(1 for v, hdr in zip(r.tolist(), hdr_txt) if hdr and not is_blank(v) and _label_norm(v) == hdr) >= max(2, 0.5 * sum(1 for x in hdr_txt if x)), axis=1)
    if rep.any():
        log.add("encabezado_repetido", celdas=int(rep.sum()), detalle="Filas que repetían el encabezado dentro de los datos")
        df = df.loc[~rep]
    return Extracted(df=df, titulo=titulo, header_rows=list(range(h, first_data)), original_names=originals, names=names, has_header=True)


def extract_groups(df: pd.DataFrame, names: list, log: ChangeLog) -> pd.DataFrame:
    """Filas-título de grupo (p. ej. 'Graneles', 'Iniciadores') → columna 'grupo' propagada hacia abajo."""
    if len(df) < 6 or df.shape[1] < 4:
        return df
    nb = ~blank_mask(df)
    counts = nb.sum(axis=1)
    if counts.median() < 3:
        return df
    label_col = next((j for j in range(df.shape[1]) if df.iloc[:, j].map(lambda v: isinstance(v, str)).mean() > 0.5), None)
    if label_col is None:
        return df
    only_label = (counts == 1) & nb.iloc[:, label_col]
    flags = only_label.tolist()
    has_data = (counts >= 3).tolist()
    headers = []
    for i, f in enumerate(flags):
        if not f or i + 1 >= len(flags):
            continue
        prev_ok = i == 0 or has_data[i - 1]
        if prev_ok and has_data[i + 1]:
            headers.append(i)
    if len(headers) < 2:
        return df
    grupo, cur = [], None
    hset = set(headers)
    for i in range(len(df)):
        if i in hset:
            cur = df.iat[i, label_col]
        grupo.append(cur)
    out = df.copy()
    out.insert(0, "__grupo", grupo)
    out = out.iloc[[i for i in range(len(out)) if i not in hset]]
    log.add("grupo_extraido", celdas=len(headers), detalle=f"{len(headers)} filas-título ({', '.join(str(df.iat[i, label_col]) for i in headers[:4])}…) convertidas en la columna 'grupo'")
    return out
