"""Reglas de dominio, valores atípicos y consistencia de totales."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .changelog import ChangeLog
from .config import DOMAIN_RULES, CleaningConfig


def apply_domain_rules(df: pd.DataFrame, cfg: CleaningConfig, log: ChangeLog) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if str(s.dtype) == "datetime64[ns]" or not pd.api.types.is_numeric_dtype(s):
            if "datetime" in str(s.dtype):
                lo, hi = pd.Timestamp("1990-01-01"), pd.Timestamp("2035-12-31")
                bad = s.notna() & ((s < lo) | (s > hi))
                for i in s.index[bad][:8]:
                    log.issue(i, col, s[i], "fecha_fuera_de_rango", "anulado" if cfg.anular_imposibles else "marcado", "Fuera de 1990–2035")
                if bad.any():
                    log.add("fecha_fuera_de_rango", col, int(bad.sum()), s.index[bad][0], s[bad].iloc[0], "", "Fecha fuera de 1990–2035", "aviso")
                    if cfg.anular_imposibles:
                        out.loc[bad, col] = pd.NaT
            continue
        for pat, lo, hi, accion, desc in DOMAIN_RULES:
            if not re.search(pat, str(col)):
                continue
            bad = pd.Series(False, index=s.index)
            if lo is not None:
                bad |= s < lo
            if hi is not None:
                bad |= s > hi
            bad = bad.fillna(False)
            if bad.any():
                do = accion == "anular" and cfg.anular_imposibles
                for i in s.index[bad][:8]:
                    log.issue(i, col, s[i], "regla_dominio", "anulado" if do else "marcado", desc)
                log.add("regla_dominio", col, int(bad.sum()), s.index[bad][0], s[bad].iloc[0], "" if do else s[bad].iloc[0], desc + (" → anulado" if do else " → solo marcado"), "alerta")
                if do:
                    out.loc[bad, col] = pd.NA if str(s.dtype) == "Int64" else np.nan
            break
    return out


def detect_outliers(df: pd.DataFrame, cfg: CleaningConfig, log: ChangeLog) -> None:
    if not cfg.detectar_atipicos:
        return
    for col in df.columns:
        s = df[col]
        if not pd.api.types.is_numeric_dtype(s) or "datetime" in str(s.dtype):
            continue
        x = s.dropna().astype(float)
        if len(x) < 40 or x.nunique() < 8:
            continue
        sf = s.astype(float)
        if (x >= 0).all():
            sf = np.log10(1 + sf)   # magnitudes con cola larga (costos, tonelajes): se evalúa en escala log
            x = sf.dropna()
        med = x.median()
        mad = (x - med).abs().median()
        if mad == 0:
            continue
        z = 0.6745 * (sf - med) / mad
        bad = z.abs() > cfg.z_atipico
        if bad.any():
            for i in s.index[bad][:6]:
                log.issue(i, col, s[i], "valor_atipico", "marcado", f"z robusto (escala log) = {z[i]:.1f}")
            log.add("valor_atipico", col, int(bad.sum()), s.index[bad][0], s[bad].iloc[0], "", f"Valores a más de {cfg.z_atipico} MAD de la mediana; se conservan y se marcan para revisión", "aviso")


def check_totals(df: pd.DataFrame, log: ChangeLog) -> None:
    """'Total 2012' debe igualar la suma de los meses 2012_01…2012_12."""
    for col in df.columns:
        m = re.fullmatch(r"total_(\d{4})", str(col))
        if not m or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        year = m.group(1)
        parts = [c for c in df.columns if re.fullmatch(rf"{year}_\d{{2}}", str(c)) and pd.api.types.is_numeric_dtype(df[c])]
        if len(parts) < 2:
            continue
        calc = df[parts].sum(axis=1, min_count=1)
        tot = df[col].astype(float)
        diff = (tot - calc).abs()
        tol = np.maximum(1.0, 0.01 * tot.abs())
        biggest = df[parts].max(axis=1)
        is_sum_like = tot >= biggest - 1e-9  # un total sumado nunca es menor que su mayor mes; si lo es, es promedio/razón
        bad = tot.notna() & calc.notna() & (diff > tol) & is_sum_like
        if bad.any():
            for i in df.index[bad][:6]:
                log.issue(i, col, tot[i], "total_inconsistente", "marcado", f"Total declarado {tot[i]:,.2f} vs suma de meses {calc[i]:,.2f}")
            log.add("total_inconsistente", col, int(bad.sum()), df.index[bad][0], tot[bad].iloc[0], calc[bad].iloc[0], f"El total {year} no coincide con la suma de sus {len(parts)} meses (tolerancia 1%)", "alerta")
        else:
            log.add("total_verificado", col, int(tot.notna().sum()), detalle=f"Total {year} coincide con la suma de {len(parts)} meses", severidad="info")
