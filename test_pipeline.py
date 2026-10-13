"""Pruebas del motor con datos sintéticos 'sucios' (no requieren los archivos reales)."""
import io

import pandas as pd
import pytest

from arena_core import CleaningConfig, clean_all, consolidate_facts, load_bytes
from arena_core.normalize import norm_name, parse_date, parse_number

CSV = (
    "REPORTE DIARIO DE EQUIPOS - LA ARENA;;;;;\n"
    ";;;;;\n"
    "Fecha;Equipo;Operador;Horas;Tonelaje;DNI\n"
    "01/10/2012;CAMION 01;JUAN PEREZ RAMOS;11,5;1.250,50;43576313\n"
    "02/10/2012;camion 01 ;MARIA LOPEZ DIAZ;12;980;41010021\n"
    "02/10/2012;camion 01 ;MARIA LOPEZ DIAZ;12;980;41010021\n"
    "03/10/2012;CAMION 02;PEDRO RUIZ SOTO;#REF!;-50;44651454\n"
    "04/10/2012;Camión 02;ANA TORRES VEGA;-;1 100;40709089\n"
    "Total;;;35,5;;\n"
)


def _run(csv=CSV, **cfg):
    tables, warns = load_bytes("reporte.csv", csv.encode("utf-8"))
    assert not warns
    return clean_all(tables, CleaningConfig(**cfg))[0]


def test_parse_helpers():
    assert parse_number("1.250,50") == 1250.5
    assert parse_number("1,250.50") == 1250.5
    assert parse_number("(500)") == -500
    assert parse_number("abc") is None
    assert parse_date("01/10/2012").month == 10
    assert parse_date("Jan_2012").year == 2012
    assert norm_name("Ore Total (Tonnes)") == "mineral_total_toneladas"


def test_structure_and_types():
    r = _run()
    assert r.titulo.startswith("REPORTE DIARIO")
    assert list(r.df.columns)[:2] == ["fecha", "equipo"]
    assert str(r.df["fecha"].dtype).startswith("datetime")
    assert pd.api.types.is_numeric_dtype(r.df["horas"])
    assert r.df["fecha"].is_monotonic_increasing


def test_no_total_row_no_duplicates():
    r = _run()
    assert len(r.df) == 4            # 5 datos - 1 duplicado; fila 'Total' retirada
    reglas = set(r.log["regla"])
    assert "duplicado_retirado" in reglas and "fila_total_retirada" in reglas


def test_pii_excluded_and_hashed():
    r = _run()
    assert "dni" not in r.df.columns and "operador" not in r.df.columns
    r2 = _run(pii_modo="anonimizar")
    assert r2.df["dni"].astype(str).str.startswith("P_").all()


def test_impossible_values_nulled_and_logged_not_invented():
    r = _run()
    assert r.df["tonelaje"].min() >= 0           # -50 anulado
    assert r.df["tonelaje"].isna().sum() >= 1     # queda vacío, no se inventa
    assert "regla_dominio" in set(r.log["regla"])
    assert (r.issues["accion"] == "anulado").any()
    assert r.df["horas"].isna().sum() == 2        # '#REF!' y '-' → vacío


def test_categories_unified():
    r = _run()
    assert r.df["equipo"].nunique() == 2


def test_wide_to_long_and_facts():
    csv = "Indicador,Jan_2012,Feb_2012,Mar_2012,Total 2012\nORE,10,20,30,60\nWASTE,1,2,3,99\n"
    tables, _ = load_bytes("plan.csv", csv.encode())
    r = clean_all(tables)[0]
    assert r.long is not None and len(r.long) == 6
    assert set(r.long["periodo"].dt.month) == {1, 2, 3}
    facts = consolidate_facts([r])
    assert len(facts) == 6
    # total inconsistente en WASTE (99 ≠ 6) debe quedar marcado
    assert "total_inconsistente" in set(r.log["regla"])


def test_multiheader():
    csv = "Pit,Banco,Jan_2012,,Feb_2012,\n,,ORE,WASTE,ORE,WASTE\nF1,3500,10,5,11,6\nF1,3494,12,7,13,8\nF2,3500,9,4,8,3\n"
    tables, _ = load_bytes("b.csv", csv.encode())
    r = clean_all(tables)[0]
    assert "2012_01_mineral" in r.df.columns and "2012_02_desmonte" in r.df.columns


def test_encoding_latin1():
    csv = "Descripción,Cantidad\nEmulsión,5\nNitrato,7\nBooster,9\nCebo,1\nMecha,2\nGuía,3\n"
    tables, w = load_bytes("l.csv", csv.encode("cp1252"))
    r = clean_all(tables)[0]
    assert "Emulsión" in r.df["descripcion"].tolist()


def test_unsupported_format_warns():
    t, w = load_bytes("x.pdf", b"%PDF")
    assert not t and w
