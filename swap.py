"""
Swap de lignes : quantités équivalentes en montant ou en sensibilité.

Univers : lignes de base_titre.xlsx valorisables et non échues à la date de valorisation, valorisées
sur la courbe BKAM (pricer.valoriser : mêmes règles que le Pricer ; amortissable linéaire, délai de grâce
DECALAG_AMORT de la base).

- Montant : Q2 = Q1 · P1 / P2
- Sensi   : Q2 = Q1 · P1 · S1 / (P2 · S2)          (même exposition au taux : même DV01)
P = prix plein unitaire (coupon couru inclus), S = sensibilité calculée sur l'échéancier.
Les titres se négocient à l'unité : la quantité exacte est arrondie à l'entier le plus proche,
et l'écart résiduel (montant, DV01) est affiché.
"""

from __future__ import annotations

import threading
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

import base_courbe as db
import base_titre as BT
import pricer as PR

TYPES = PR.TYPES


def courbe_en_base(d: str) -> tuple[pd.DataFrame, str]:
    """Points BKAM de la date (ou, à défaut, les 9 ténors historisés)."""
    with db.connexion() as conn:
        brute = db.lire_courbe_bkam(conn, d)
        tenors = db.lire_tenors(conn, d)
    jour = pd.Timestamp(d).strftime("%d/%m/%Y")
    if len(brute) >= 2:
        return PR.df_points(brute), f"Courbe BKAM du {jour} · {len(brute)} points de marché."
    if tenors:
        return PR.df_tenors(tenors), f"Pas de points de marché le {jour} : courbe des 9 ténors historisés."
    return pd.DataFrame(), f"Aucune courbe en base pour le {jour}."


def _parametres(f: dict, valo: date) -> dict:
    return {**{k: f[k] for k in ("type", "nominal", "tf", "prime", "em", "jouis", "ech", "revisions", "freq",
                                 "rev_amorti", "base_coupon", "valo_zero")},
            "valo": valo, "amortissement": "lineaire", "montant": None, "grace": f.get("grace"),
            "coupon_fixe": f.get("coupon_fixe"), "echeancier": f.get("echeancier"),
            "nominal_ref": f.get("nominal_ref")}


@lru_cache(maxsize=6)
def _univers(valo_iso: str, courbe_iso: str, mtime_base: int, version_db: int) -> tuple[pd.DataFrame, str]:
    courbe, info = courbe_en_base(courbe_iso)
    if courbe.empty:
        return pd.DataFrame(), info
    valo = date.fromisoformat(valo_iso)
    lignes = []
    for code in BT.base().index:
        f = BT.fiche(code)
        debut = f["em"] or f["jouis"] if f else None
        if not (f and f["valorisable"] and f["ech"] and f["ech"] > valo and debut and debut <= valo) or f.get("valo_zero"):
            continue
        try:
            v = PR.valoriser(_parametres(f, valo), courbe)
        except Exception:
            continue
        lignes.append({
            "code": code, "description": f["description"], "isin": f["isin"], "emetteur": f["emetteur"],
            "classe": f["classe"], "categorie": f["categorie"], "type": f["type"], "type_lib": TYPES[f["type"]]
            + (" amort." if f["rev_amorti"] else ""), "type_taux": f["type_taux"],
            "spread": f["prime"], "tf": f["tf"], "echeance": pd.Timestamp(f["ech"]),
            "echeance_eff": pd.Timestamp(v["ech_eff"]), "maturite": v["jours"] / 365.25,
            "nominal": f["nominal"], "prix": v["valeur"], "ppc": v["ppc_pct"], "cc": v["cc"],
            "taux": v["taux"], "sensibilite": v["sensibilite"], "duration": v["duration"],
        })
    return pd.DataFrame(lignes).set_index("code", drop=False), info


_VERROU = threading.Lock()


def univers(valo_iso: str, courbe_iso: str) -> tuple[pd.DataFrame, str]:
    """Univers valorisé (calculé une seule fois par date, même si plusieurs callbacks le demandent ensemble)."""
    b = BT.CHEMIN.stat().st_mtime_ns if BT.CHEMIN.exists() else 0
    with _VERROU:
        return _univers(valo_iso, courbe_iso, b, db.version_base())


def quantites(l1: pd.Series, q1: float, l2: pd.Series) -> dict:
    """Quantités de la ligne 2 équivalentes à q1 titres de la ligne 1, en montant et en sensibilité."""
    m1 = q1 * l1["prix"]
    e1 = m1 * l1["sensibilite"]                                   # exposition = montant × sensibilité
    res = {"montant_1": m1, "dv01_1": e1 * 1e-4}
    for cle, cible, par in (("montant", m1, l2["prix"]), ("sensi", e1, l2["prix"] * l2["sensibilite"])):
        exact = cible / par if par else np.nan
        arrondi = float(np.round(exact)) if np.isfinite(exact) else np.nan
        m2 = arrondi * l2["prix"]
        res[cle] = {"exacte": exact, "arrondie": arrondi, "montant_2": m2,
                    "dv01_2": m2 * l2["sensibilite"] * 1e-4,
                    "ecart_montant": m2 - m1, "ecart_dv01": (m2 * l2["sensibilite"] - e1) * 1e-4}
    return res


def ajouter_quantites(df: pd.DataFrame, l1: pd.Series, q1: float) -> pd.DataFrame:
    """Colonnes Q2 (montant et sensi) pour un tableau de lignes candidates."""
    df = df.copy()
    m1, e1 = q1 * l1["prix"], q1 * l1["prix"] * l1["sensibilite"]
    df["q_montant"] = m1 / df["prix"]
    df["q_sensi"] = e1 / (df["prix"] * df["sensibilite"]).replace(0, np.nan)
    df["q_montant_arr"] = df["q_montant"].round()
    df["q_sensi_arr"] = df["q_sensi"].round()
    df["montant_sensi"] = df["q_sensi_arr"] * df["prix"]
    df["ecart_sensi"] = df["sensibilite"] - l1["sensibilite"]
    df["ecart_maturite"] = df["maturite"] - l1["maturite"]
    return df
