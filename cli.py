"""CLI:  python -m arena_core.cli <carpeta|archivo> --out salida/  (mismo motor que la interfaz web)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CleaningConfig
from .export import build_workbook, build_zip, summary_frame
from .ingest import load_folder, load_inputs
from .pipeline import clean_all


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Limpia y estructura CSV/Excel de La Arena")
    ap.add_argument("entrada", help="Carpeta o archivo (csv/xlsx/zip)")
    ap.add_argument("--out", default="salida", help="Carpeta de salida")
    ap.add_argument("--pii", choices=["excluir", "anonimizar", "conservar"], default="excluir")
    ap.add_argument("--sep", default=",", help="Separador CSV de salida (use ';' para Excel en español)")
    args = ap.parse_args(argv)

    p = Path(args.entrada)
    tables, warns = load_folder(str(p)) if p.is_dir() else load_inputs([(p.name, p.read_bytes())])
    for w in warns:
        print("AVISO:", w, file=sys.stderr)
    cfg = CleaningConfig(pii_modo=args.pii)
    results = clean_all(tables, cfg, progress=lambda f, n: print(f"[{f:4.0%}] {n}"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "entregables.zip").write_bytes(build_zip(results, sep=args.sep))
    (out / "Base_Reportes_Operativos_LaArena.xlsx").write_bytes(build_workbook(results))
    summary_frame(results).to_csv(out / "indice_tablas.csv", index=False, encoding="utf-8-sig")
    print(f"Listo: {len(results)} tablas → {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
