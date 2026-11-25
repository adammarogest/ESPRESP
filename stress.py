"""
Stress test de la courbe BKAM.

Toutes les interpolations passent par taux_courbe / taux_courbe_brut de COURBE.py :
mêmes règles que TauxCourbe (plat sous le premier point, interpolation linéaire, conversion
monétaire ↔ actuariel autour d'un an, extrapolation sur les deux derniers points).

Deux sens :
- Courbe -> ténors : on modifie des points de la courbe BKAM, les ténors sont recalculés.
- Ténor -> courbe  : on fixe le taux voulu pour un ténor ; on cherche le déplacement des points
  de la courbe qui, après interpolation, redonne exactement ce taux (résolution numérique).
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from scipy.optimize import brentq

import base_courbe as db

try:                                    # le script s'appelle COURBE.py ou Courbe.py
    import COURBE as courbe
except ImportError:
    import Courbe as courbe

TENORS = db.TENORS
MODES = {
    "encadrants": "Points encadrants",
    "proche": "Point le plus proche",
    "parallele": "Translation parallèle",
    "inserer": "Insérer un point",
}


# ---------------------------------------------------------------------------
# Courbe <-> tableau de points
# ---------------------------------------------------------------------------

def vers_df(points: list[dict]) -> pd.DataFrame:
    """Lignes du tableau -> DataFrame au format attendu par COURBE.taux_courbe (taux en %)."""
    df = pd.DataFrame(points)
    if df.empty:
        return pd.DataFrame(columns=["maturite_residuelle", "taux_moyen_pondere"])
    df = df.assign(maturite_residuelle=pd.to_numeric(df["maturite"], errors="coerce"),
                   taux_moyen_pondere=pd.to_numeric(df["taux"], errors="coerce"))
    df = df.dropna(subset=["maturite_residuelle", "taux_moyen_pondere"])
    df = df[df["maturite_residuelle"] > 0]
    return df.sort_values("maturite_residuelle").reset_index(drop=True)


def _date(datevl):
    """COURBE accepte un datetime ou 'JJ/MM/AAAA' : on lui passe toujours un datetime."""
    return pd.Timestamp(datevl).to_pydatetime()


def taux_a(df: pd.DataFrame, jours: float, datevl) -> float:
    """Taux interpolé (%, non arrondi) à une maturité en jours."""
    return courbe.taux_courbe_brut(df, float(jours), _date(datevl)) * 100


def tableau_tenors(df: pd.DataFrame, datevl) -> dict[str, float]:
    """Taux des ténors arrondis à 3 décimales, comme COURBE.tableau_tenors."""
    d = _date(datevl)
    return {t: courbe.taux_courbe(df, j, d) for t, j in TENORS.items()}


def courbe_fine(df: pd.DataFrame, datevl, n: int = 240) -> tuple[np.ndarray, np.ndarray]:
    """Courbe interpolée sur une grille de maturités (jours), pour le graphique."""
    j_max = max(float(df["maturite_residuelle"].max()), max(TENORS.values()))
    grille = np.unique(np.concatenate([np.geomspace(1, j_max, n), list(TENORS.values()),
                                       df["maturite_residuelle"].to_numpy(dtype=float),
                                       [364, 365, 366, 367]]))
    return grille, np.array([taux_a(df, j, datevl) for j in grille])


# ---------------------------------------------------------------------------
# Chocs
# ---------------------------------------------------------------------------

def choc_lineaire(points: list[dict], court_pb: float, long_pb: float) -> list[dict]:
    """
    Choc appliqué à chaque point, linéaire en maturité entre le premier point (court_pb)
    et le dernier (long_pb). Parallèle si les deux sont égaux, pentification si long > court.
    """
    mats = [float(p["maturite"]) for p in points if p.get("maturite") not in (None, "")]
    if not mats:
        return points
    m0, m1 = min(mats), max(mats)
    sortie = []
    for p in points:
        q = dict(p)
        if q.get("maturite") not in (None, "") and q.get("taux") not in (None, ""):
            w = 0.0 if m1 == m0 else (float(q["maturite"]) - m0) / (m1 - m0)
            q["taux"] = float(q["taux"]) + (court_pb + w * (long_pb - court_pb)) / 100
        sortie.append(q)
    return sortie


# ---------------------------------------------------------------------------
# Ténor -> courbe (résolution inverse)
# ---------------------------------------------------------------------------

def _indices(mats: np.ndarray, jours: float, mode: str) -> list[int]:
    n = len(mats)
    if mode == "parallele":
        return list(range(n))
    if mode == "proche":
        return [int(np.argmin(np.abs(mats - jours)))]
    # Points qui servent réellement à l'interpolation de TauxCourbe pour cette maturité
    if jours <= mats[0]:
        return [0]
    if jours >= mats[-1]:
        return [n - 2, n - 1]
    i = int(np.searchsorted(mats, jours, side="right") - 1)
    return [i, i + 1]


def resoudre(points: list[dict], tenor: str, cible: float, datevl, mode: str) -> tuple[list[dict], str]:
    """
    Déplace des points de la courbe pour que le ténor interpolé vaille exactement `cible` (%).
    Renvoie (nouveaux points, message). Lève ValueError si la cible est inatteignable.
    """
    jours = TENORS[tenor]
    df = vers_df(points)
    if len(df) < 2:
        raise ValueError("Il faut au moins deux points dans la courbe.")
    avant = taux_a(df, jours, datevl)

    if mode == "inserer":
        mats = df["maturite_residuelle"].to_numpy(dtype=float)
        sortie = [dict(p) for p in points]
        existant = [p for p in sortie if p.get("maturite") not in (None, "") and float(p["maturite"]) == jours]
        if existant:
            existant[0]["taux"] = cible
            msg = f"Le point à {jours} j existe déjà : son taux passe à {cible:.3f} %."
        else:
            ref = next((p for p in points if p.get("valeur")), {})
            sortie.append({"maturite": jours, "taux": cible, "taux_orig": None,
                           "valeur": ref.get("valeur"), "nouveau": True})
            msg = f"Point ajouté à {jours} j ({tenor}) au taux de {cible:.3f} %."
        return sortie, msg

    mats = df["maturite_residuelle"].to_numpy(dtype=float)
    idx = _indices(mats, jours, mode)
    cles = set(mats[idx])

    def decaler(delta: float) -> pd.DataFrame:
        d = df.copy()
        masque = d["maturite_residuelle"].isin(cles)
        d.loc[masque, "taux_moyen_pondere"] = d.loc[masque, "taux_moyen_pondere"] + delta
        return d

    def ecart(delta: float) -> float:
        return taux_a(decaler(delta), jours, datevl) - cible

    if abs(ecart(0.0)) < 1e-9:
        return points, f"{tenor} vaut déjà {cible:.3f} %."
    if abs(ecart(0.5) - ecart(-0.5)) < 1e-10:
        raise ValueError(f"Avec le mode « {MODES[mode]} », le point déplacé n'intervient pas dans "
                         f"l'interpolation du {tenor}. Choisissez un autre mode.")
    try:
        delta = brentq(ecart, -20.0, 20.0, xtol=1e-12)
    except ValueError:
        raise ValueError(f"Cible {cible:.3f} % inatteignable pour le {tenor} avec ce mode.") from None

    sortie = []
    for p in points:
        q = dict(p)
        if q.get("maturite") not in (None, "") and float(q["maturite"]) in cles:
            q["taux"] = float(q["taux"]) + delta
        sortie.append(q)
    noms = ", ".join(f"{int(m)} j" for m in sorted(cles)) if mode != "parallele" else "tous les points"
    msg = (f"{tenor} : {avant:.3f} % → {cible:.3f} %. Déplacement de {delta * 100:+.2f} pb "
           f"appliqué à {noms}.")
    return sortie, msg


# ---------------------------------------------------------------------------
# Préparation des lignes
# ---------------------------------------------------------------------------

def lignes_depuis_bkam(brute: pd.DataFrame) -> list[dict]:
    lignes = []
    for r in brute.itertuples():
        lignes.append({"maturite": int(r.maturite_residuelle), "taux_orig": float(r.taux), "taux": float(r.taux),
                       "valeur": pd.Timestamp(r.date_valeur).date().isoformat()
                       if pd.notna(r.date_valeur) else None})
    return lignes


def lignes_depuis_tenors(taux: dict, datevl) -> list[dict]:
    """Repli : courbe reconstituée à partir des 9 ténors historisés."""
    return [{"maturite": j, "taux_orig": float(taux[t]), "taux": float(taux[t]),
             "valeur": pd.Timestamp(datevl).date().isoformat()} for t, j in TENORS.items()]


def normaliser(lignes: list[dict], datevl) -> list[dict]:
    """Recalcule échéance et écart, trie par maturité ; garde les lignes en cours de saisie."""
    sortie = []
    for p in lignes:
        q = dict(p)
        m = pd.to_numeric(q.get("maturite"), errors="coerce")
        t = pd.to_numeric(q.get("taux"), errors="coerce")
        o = pd.to_numeric(q.get("taux_orig"), errors="coerce")
        q["maturite"] = None if pd.isna(m) else int(m)
        q["taux"] = None if pd.isna(t) else round(float(t), 4)     # 0,01 pb : affichage lisible
        base = pd.Timestamp(q.get("valeur") or datevl)
        q["echeance"] = (base + timedelta(days=int(m))).strftime("%d/%m/%Y") if pd.notna(m) else ""
        q["ecart"] = round((q["taux"] - o) * 100, 2) if q["taux"] is not None and pd.notna(o) else None
        q["statut"] = "nouveau" if pd.isna(o) else ("modifie" if q["ecart"] not in (None, 0) else "")
        sortie.append(q)
    return sorted(sortie, key=lambda q: (q["maturite"] is None, q["maturite"] or 0))
