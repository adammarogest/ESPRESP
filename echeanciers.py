"""
Échéanciers imposés par l'émetteur : ECHEANCIERS.xlsm (même dossier que l'application).

Format : pour chaque titre, une ligne d'en-tête avec le code (ou l'ISIN) en colonne A et les libellés
« Date tombée | Amorti | Restant | Coupon » en B:E, puis une ligne par date de tombée (B date, C capital
amorti, D capital restant dû après la tombée, E coupon), montants pour le nominal de la base titres.
Une ligne vide sépare les titres. Le fichier est relu automatiquement dès qu'il est modifié.

Quand un titre a un échéancier dans ce fichier, ses flux ne sont pas reconstitués : le pricer lit les
tombées postérieures à la date de valorisation et les actualise avec la même logique que les autres lignes.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

CHEMIN = Path(__file__).resolve().parent / "ECHEANCIERS.xlsm"


def _cle(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


def _nombre(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


@lru_cache(maxsize=4)
def _charger(chemin: str, mtime: int) -> dict[str, list[tuple]]:
    import openpyxl
    wb = openpyxl.load_workbook(chemin, data_only=True, read_only=True)
    blocs: dict[str, list[tuple]] = {}
    for ws in wb.worksheets:
        courant = None
        for ligne in ws.iter_rows(values_only=True):
            a, b, c, d, e = (list(ligne) + [None] * 5)[:5]
            if a not in (None, "") and isinstance(b, str):          # en-tête d'un titre
                courant = _cle(a)
                blocs[courant] = []
                continue
            if courant and isinstance(b, (datetime, date)):
                jour = b.date() if isinstance(b, datetime) else b
                blocs[courant].append((jour, _nombre(c), _nombre(d), _nombre(e)))
            elif not any(x not in (None, "") for x in (a, b, c, d, e)):
                courant = None                                      # ligne vide : fin du bloc
    wb.close()
    return {k: sorted(v) for k, v in blocs.items() if v}


def tous() -> dict[str, list[tuple]]:
    """{code ou ISIN : [(date, amorti, restant, coupon), …]} ; vide si le fichier est absent ou illisible."""
    if not CHEMIN.exists():
        return {}
    try:
        return _charger(str(CHEMIN), CHEMIN.stat().st_mtime_ns)
    except Exception:
        return {}


def echeancier(code: str, isin: str | None = None) -> list[tuple] | None:
    """Échéancier du titre (recherché par code puis par ISIN), ou None."""
    e = tous()
    return e.get(_cle(code)) or (e.get(_cle(isin)) if isin else None)


def info() -> str:
    e = tous()
    if not CHEMIN.exists():
        return "ECHEANCIERS.xlsm absent du dossier."
    return f"ECHEANCIERS.xlsm : {len(e)} échéancier(s) imposé(s)."
