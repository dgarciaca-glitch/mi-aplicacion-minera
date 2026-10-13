"""Digitalizador La Arena — motor de limpieza y estructuración de datos operativos."""
from .config import CleaningConfig
from .ingest import RawTable, load_bytes, load_folder, load_inputs
from .pipeline import CleanResult, clean_all, clean_table, consolidate_facts

__all__ = ["CleaningConfig", "RawTable", "load_bytes", "load_folder", "load_inputs", "CleanResult", "clean_all", "clean_table", "consolidate_facts"]
__version__ = "1.0.0"
