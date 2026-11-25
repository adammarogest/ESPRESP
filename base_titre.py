"""
Base des titres (base_titre.xlsx, à côté de l'application).

Le fichier est relu automatiquement dès qu'il change (date de modification). Les colonnes sont
repérées par leur nom (CODE, CODE_ISIN, DESCRIPTION, CLASSE, CATEGORIE, EMETTEUR, FORME_DETENTION,
NOMINAL, SPREAD_EMISSION, TYPE_TAUX, VALEUR_TAUX, PERIODICITE_COUPON, PERIODICITE_REMBOU,
DATE_EMISSION, DATE_JOUISSANCE, DATE_ECHEANCE, DATE_MAJ, METHODE_VALO, ...).

Correspondance avec le pricer :
- TYPE_TAUX = REV                         -> révisable (in fine jusqu'à la prochaine révision ;
                                             s'il s'amortit, sur le capital restant dû)
- PERIODICITE_REMBOU ≠ FIN ou METHODE_VALO = ZC -> amortissable (valo_ligne_zc)
- sinon                                   -> in fine (valo_ligne)
- SPREAD_EMISSION (pb) vide               -> prime de risque 0
- Dates de révision : DATE_REVISION (prochaine révision) puis les suivantes à la périodicité du
  coupon ; si la colonne est vide, dates de coupon postérieures à DATE_MAJ.
- Délai de grâce de l'amortissement : DECALAG_AMORT (années), vide ou 0 = aucun.
- Base du coupon : METHODE_COUPON 364/360, 365/360, 366/360 -> exact/360 (après la première
  période) ; R/R -> exact/exact.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

import echeanciers as E

CHEMIN = Path(__file__).resolve().parent / "base_titre.xlsx"

# Lignes toujours valorisées à 0 (règle de gestion) : code base et ISIN
VALO_ZERO_CODES = {"9435"}
VALO_ZERO_ISIN = {"MA0000094351"}

# Coupons imposés (règle de gestion) : montant de chaque coupon en MAD par titre (nominal de la base),
# quel que soit le nombre de jours de la période. 153159 : 2 780 sur les deux coupons, à la demande de l'émetteur.
COUPONS_FIXES = {"153159": 2780.0}

# Délais de grâce absents de DECALAG_AMORT (années) : 9431 amortie après 2 ans de grâce
GRACES = {"9431": 2.0}

FREQ = {"AN": "annuel", "SEM": "semestriel", "TRI": "trimestriel"}
MOIS = {"annuel": 12, "semestriel": 6, "trimestriel": 3}
DETENTION = {"VON": "Au porteur", "VEN": "Nominative"}
TYPE_TAUX = {"FIX": "Fixe", "REV": "Révisable"}
PERIODICITES = {"AN": "Annuelle", "SEM": "Semestrielle", "TRI": "Trimestrielle", "MEN": "Mensuelle",
                "FIN": "In fine"}


def _code(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _date(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str):
        v = v.strip()
        d = pd.to_datetime(v, format="%Y-%m-%d", errors="coerce")
        if pd.isna(d):
            d = pd.to_datetime(v, dayfirst=True, errors="coerce")
    else:
        d = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(d) else d.date()


def _texte(v) -> str:
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()


@lru_cache(maxsize=2)
def _charger(chemin: str, mtime: int) -> pd.DataFrame:
    df = pd.read_excel(chemin, dtype=object)
    df.columns = [str(c).strip().upper() for c in df.columns]
    if "CODE" not in df.columns:
        raise ValueError("base_titre.xlsx : colonne CODE introuvable")
    df["CODE"] = df["CODE"].map(_code)
    df = df[df["CODE"] != ""].drop_duplicates("CODE", keep="last")
    return df.set_index("CODE", drop=False)


def base(chemin: Path | str = CHEMIN) -> pd.DataFrame:
    """Base des titres (vide si le fichier est absent)."""
    chemin = Path(chemin)
    if not chemin.exists():
        return pd.DataFrame()
    return _charger(str(chemin), chemin.stat().st_mtime_ns)


def info_base() -> str:
    b = base()
    if b.empty:
        return f"{CHEMIN.name} introuvable"
    maj = pd.Timestamp(CHEMIN.stat().st_mtime, unit="s").strftime("%d/%m/%Y %H:%M")
    return f"{len(b)} titres · fichier du {maj}"


def options_codes() -> list[dict]:
    """Pour la liste de suggestions : code + description."""
    b = base()
    if b.empty:
        return []
    return [{"value": c, "label": f"{c} — {_texte(d)}"}
            for c, d in zip(b["CODE"], b.get("DESCRIPTION", pd.Series("", index=b.index)))]


def _val(ligne: pd.Series, *noms, defaut=None):
    for nom in noms:
        if nom in ligne.index:
            v = ligne[nom]
            if not (v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and not v.strip())):
                return v
    return defaut


def _num(v, defaut=None):
    x = pd.to_numeric(v, errors="coerce") if v is not None else np.nan
    return defaut if pd.isna(x) else float(x)


def dates_revision(jouis, ech, freq: str, depuis=None) -> list:
    """Dates de coupon (= révisions) strictement entre `depuis` (ou la jouissance) et l'échéance."""
    if not (jouis and ech):
        return []
    pas, d, k, res = MOIS.get(freq, 12), jouis, 1, []
    debut = depuis or jouis
    while True:
        d = jouis + relativedelta(months=pas * k)
        if d >= ech:
            break
        if d > debut:
            res.append(d)
        k += 1
    return res


def fiche(code: str) -> dict | None:
    """Caractéristiques d'un titre de la base, prêtes pour le pricer, ou None si le code est inconnu."""
    b = base()
    code = _code(code)
    if b.empty or code not in b.index:
        return None
    l = b.loc[code]
    alertes = []

    type_taux = _texte(_val(l, "TYPE_TAUX")).upper()
    remb = _texte(_val(l, "PERIODICITE_REMBOU")).upper()
    per_coupon = _texte(_val(l, "PERIODICITE_COUPON")).upper()
    methode = _texte(_val(l, "METHODE_VALO")).upper()
    valorisable = bool(type_taux) and _val(l, "DATE_ECHEANCE") is not None

    amorti = remb not in ("", "FIN")
    if type_taux == "REV":
        type_ = "revisable"
    elif amorti or methode == "ZC":
        type_ = "amortissable"
    else:
        type_ = "in_fine"

    freq = FREQ.get(per_coupon, "annuel")
    if per_coupon and per_coupon not in FREQ:
        alertes.append(f"Périodicité de coupon {per_coupon} non gérée par le pricer : annuelle retenue.")
    if type_ in ("in_fine", "revisable") and per_coupon in ("SEM", "TRI", "MEN") and not amorti:
        alertes.append("Coupons non annuels : valo_ligne suppose des coupons annuels, à vérifier.")
    if type_ == "amortissable" and methode == "AA":
        alertes.append("Méthode de la base AA pour une ligne amortie : valorisée ici en ZC (valo_ligne_zc).")
    if methode in ("AC", "CC"):
        alertes.append(f"Méthode de valorisation {methode} : non prévue, valorisation par défaut appliquée.")

    em, jouis, ech = _date(_val(l, "DATE_EMISSION")), _date(_val(l, "DATE_JOUISSANCE")), _date(_val(l, "DATE_ECHEANCE"))
    jouis = jouis or em
    em = em or jouis
    maj = _date(_val(l, "DATE_MAJ"))

    revisions = []
    if type_ == "revisable":
        col_rev = "DATE_REVISION" if "DATE_REVISION" in l.index else next((c for c in l.index if "REVISION" in c), None)
        rev0 = _date(_val(l, col_rev)) if col_rev else None
        if rev0 and ech:
            # Prochaine révision de la base, puis révisions suivantes à la périodicité du coupon
            # (si la date de la base est dépassée, on avance jusqu'aux révisions à venir)
            pas, k = MOIS.get(freq, 12), 0
            while (d := rev0 + relativedelta(months=pas * k)) < ech:
                revisions.append(d)
                k += 1
        else:
            revisions = dates_revision(jouis, ech, freq, depuis=maj if maj and jouis and maj > jouis else None)

    grace = GRACES.get(code) or _num(_val(l, "DECALAG_AMORT"), 0.0)
    spread = _num(_val(l, "SPREAD_EMISSION"), 0.0)
    nominal = _num(_val(l, "NOMINAL"), None)
    if not nominal:
        alertes.append("Nominal absent de la base : 100 000 MAD par défaut.")
        nominal = 100000.0

    isin = _texte(_val(l, "CODE_ISIN"))
    valo_zero = code in VALO_ZERO_CODES or isin in VALO_ZERO_ISIN
    ech_impose = E.echeancier(code, isin)
    if ech_impose:
        alertes.insert(0, f"Échéancier imposé par l'émetteur lu dans ECHEANCIERS.xlsm ({len(ech_impose)} tombées) : "
                          "flux non reconstitués.")
    coupon_fixe = COUPONS_FIXES.get(code)
    if coupon_fixe:
        montant = f"{coupon_fixe:,.2f}".replace(",", " ").replace(".", ",")
        alertes.insert(0, f"Règle de gestion : chaque coupon est fixé à {montant} MAD par titre "
                          "(demande de l'émetteur), sans prorata des jours.")
    if valo_zero:
        alertes.insert(0, "Règle de gestion : ligne toujours valorisée à 0 (portefeuilles, contrôle de valorisation, swap).")
    return {
        "code": code, "valorisable": valorisable, "alertes": alertes, "valo_zero": valo_zero,
        # Pricing
        "type": type_, "rev_amorti": type_ == "revisable" and amorti,
        "grace": grace if grace and amorti else None,
        "base_coupon": "360" if _texte(_val(l, "METHODE_COUPON")).endswith("/360") else "R/R",
        "coupon_fixe": coupon_fixe / nominal if coupon_fixe else None,
        "echeancier": ech_impose, "nominal_ref": nominal,
        "nominal": nominal, "prime": spread, "tf": _num(_val(l, "VALEUR_TAUX"), 0.0),
        "em": em, "jouis": jouis, "ech": ech, "revisions": revisions, "freq": freq,
        # Information
        "description": _texte(_val(l, "DESCRIPTION")), "isin": _texte(_val(l, "CODE_ISIN")),
        "classe": _texte(_val(l, "CLASSE")), "categorie": _texte(_val(l, "CATEGORIE")),
        "emetteur": _texte(_val(l, "EMETTEUR")),
        "detention": DETENTION.get(_texte(_val(l, "FORME_DETENTION")).upper(), _texte(_val(l, "FORME_DETENTION"))),
        "type_taux": TYPE_TAUX.get(type_taux, type_taux or "—"), "methode": methode or "—",
        "periodicite_coupon": PERIODICITES.get(per_coupon, per_coupon or "—"),
        "periodicite_rembou": PERIODICITES.get(remb, remb or "—"),
        "base_calcul": _texte(_val(l, "BASE_CALCUL")), "methode_coupon": _texte(_val(l, "METHODE_COUPON")),
        "date_maj": maj, "type_cotation": _texte(_val(l, "TYPE_COTATION")),
    }
