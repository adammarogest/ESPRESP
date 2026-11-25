"""
Pricer de BDT : prix, échéancier et indicateurs d'analyse statique.

Le pricing lui-même reprend les fonctions de taux_courbe.py, sans les modifier :
- In fine et révisable : valo_ligne (convention VL : tous les flux actualisés au taux de la
  maturité ; un révisable est valorisé comme un in fine échéant à sa prochaine révision)
- Amortissable          : valo_ligne_zc (chaque flux actualisé au taux zéro-coupon de sa maturité)

Taux de rendement, trois sources :
- Courbe BKAM        : maturité résiduelle interpolée sur la courbe de marché (taux_courbe)
- Ténors encadrants  : même interpolation, mais sur les 9 ténors seulement, éventuellement
                       renégociés ; le taux dépend alors des deux ténors qui encadrent la maturité
- Taux négocié       : saisi librement
Pour un amortissable, « courbe » et « ténors » donnent la courbe zéro-coupon utilisée flux par flux ;
le taux négocié est un taux actuariel unique appliqué à tous les flux.

Indicateurs calculés à partir de l'échéancier : duration de Macaulay, sensibilité, convexité,
TRI, coupon couru, prix pied de coupon (PPC), DV01, rendement courant, point mort, chocs de taux.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import brentq

import base_courbe as db
import taux_courbe as tc

TENORS = db.TENORS
TYPES = {"in_fine": "In fine", "revisable": "Révisable", "amortissable": "Amortissable"}
SOURCES = {"courbe": "Courbe BKAM", "tenors": "Ténors encadrants", "negocie": "Taux négocié"}
FREQUENCES = {"annuel": "Annuel", "semestriel": "Semestriel", "trimestriel": "Trimestriel"}
AMORTISSEMENTS = {"lineaire": "Linéaire", "constant": "Constant"}
CHOCS_PB = [-100, -50, -25, 25, 50, 100]


# ---------------------------------------------------------------------------
# Courbes
# ---------------------------------------------------------------------------

def df_points(lignes: list[dict] | pd.DataFrame) -> pd.DataFrame:
    """Points de courbe (maturité en jours, taux en %) au format de taux_courbe."""
    df = pd.DataFrame(lignes)
    df = df.rename(columns={"maturite": "maturite_residuelle", "taux": "taux_moyen_pondere"})
    df = df[["maturite_residuelle", "taux_moyen_pondere"]].apply(pd.to_numeric, errors="coerce").dropna()
    return df.sort_values("maturite_residuelle").reset_index(drop=True)


def df_tenors(taux: dict) -> pd.DataFrame:
    """Courbe réduite aux 9 ténors."""
    return pd.DataFrame({"maturite_residuelle": list(TENORS.values()),
                         "taux_moyen_pondere": [float(taux[t]) for t in TENORS]})


def tenors_encadrants(jours: float) -> tuple[str, str]:
    """Les deux ténors entre lesquels taux_courbe interpole cette maturité."""
    noms, js = list(TENORS), list(TENORS.values())
    if jours <= js[0]:
        return noms[0], noms[0]
    if jours >= js[-1]:
        return noms[-2], noms[-1]                     # extrapolation sur 20 ans et 30 ans
    i = int(np.searchsorted(js, jours, side="right") - 1)
    return noms[i], noms[i + 1]


def taux_interpole(df: pd.DataFrame, jours: float, valo) -> float:
    """Taux de la maturité (%, arrondi à 3 décimales comme le tableau des ténors)."""
    return tc.taux_courbe(df, float(jours), pd.Timestamp(valo).to_pydatetime())


# ---------------------------------------------------------------------------
# Échéance effective, coupon couru, échéancier d'amortissement
# ---------------------------------------------------------------------------

def echeance_effective(ech: date, valo: date, revisions) -> date:
    return min(ech, tc._prochaine_revision(revisions, valo)) if revisions else ech


def coupon_couru_vl(n: float, em: date, jouis: date, ech: date, valo: date, tf: float) -> tuple[float, date]:
    """
    Coupon couru selon ValoLigne (tf en %) : depuis le dernier coupon détaché, ou depuis la date
    d'émission avant le premier coupon ; base 360 pour une maturité initiale ≤ 1 an (titre
    monétaire), sinon 365 ou 366 (Bissextile du prochain coupon). Renvoie (coupon couru, date de départ).
    """
    a = 365 + tc.bissextile(tc._coup_ncd(valo, ech))
    matinit = (ech - em).days
    if matinit <= a:
        depart, base = em, 360
    else:
        premier = tc._coup_ncd(jouis + timedelta(days=1), ech)
        depart = em if valo < premier else tc._coup_pcd(valo, ech)
        base = a
    jours = max(0, (valo - depart).days)
    return n * tf / 100 * jours / base, depart


def echeancier_amortissable(n: float, jouis: date, ech: date, valo: date, tf: float, freq: str,
                            amortissement: str, montant: float | None, delai_grace=None) -> dict:
    """
    Reconstitue l'échéancier complet (mêmes règles que valo_ligne_zc) pour le coupon couru,
    le capital restant dû et la durée de vie moyenne.
    """
    f = {"annuel": 1, "semestriel": 2, "trimestriel": 4}[freq]
    nb = tc._coup_num(jouis, ech, f)
    dates = [tc._date_coupon(ech, nb - 1 - k, f) for k in range(nb)]
    amorts = tc._amortissements(n, dates, jouis, amortissement, montant, delai_grace)
    k0 = next(k for k, d in enumerate(dates) if d > valo)
    crd = n - sum(amorts[:k0])
    depart = dates[k0 - 1] if k0 > 0 else jouis
    base = 365 + tc.bissextile(dates[k0])
    cc = crd * tf / 100 * max(0, (valo - depart).days) / base
    restants = [(d, a) for d, a in zip(dates[k0:], amorts[k0:]) if a > 0]
    return {"crd": crd, "cc": cc, "depart": depart, "prochaine": dates[k0],
            "amorts_restants": restants, "coupon_annuel": crd * tf / 100}


# ---------------------------------------------------------------------------
# Analyse de l'échéancier
# ---------------------------------------------------------------------------

def _tri(t: np.ndarray, flux: np.ndarray, prix: float) -> float:
    """Taux actuariel unique qui égalise flux actualisés et prix (%)."""
    def f(r):
        return float((flux / (1 + r) ** t).sum()) - prix
    try:
        return brentq(f, -0.5, 1.0, xtol=1e-14) * 100
    except ValueError:
        return np.nan


def analyser(tab: pd.DataFrame, valeur: float, monetaire: bool, jours_monetaire: float | None) -> dict:
    """
    Duration de Macaulay, sensibilité et convexité à partir de l'échéancier
    (colonnes maturite_residuelle_en_annee, taux_de_valorisation, flux, flux_actualise).
    - Flux monétaire unique : S = (n/360) / (1 + y n/360), D = S (1 + y), C = 2 (n/360)² / (1 + y n/360)²
    - Flux actuariels      : D = Σ t·VA / P, S = Σ t·VA / (1 + y_i) / P, C = Σ t(t+1)·VA / (1 + y_i)² / P
      (y_i = taux propre à chaque flux : identique pour un in fine, zéro-coupon pour un amortissable)
    """
    t = tab["maturite_residuelle_en_annee"].to_numpy(dtype=float)
    va = tab["flux_actualise"].to_numpy(dtype=float)
    fl = tab["flux"].to_numpy(dtype=float)
    y = tab["taux_de_valorisation"].to_numpy(dtype=float) / 100
    if monetaire:
        n = jours_monetaire / 360
        y0 = y[0]
        sens = n / (1 + y0 * n)
        conv = 2 * n ** 2 / (1 + y0 * n) ** 2
        mac = sens * (1 + y0)                      # convention VL : D = S × (1 + y)
    else:
        mac = float((t * va).sum() / valeur)
        sens = float((t * va / (1 + y)).sum() / valeur)
        conv = float((t * (t + 1) * va / (1 + y) ** 2).sum() / valeur)
    return {"duration": mac, "sensibilite": sens, "convexite": conv, "tri": _tri(t, fl, valeur),
            "dv01": sens * valeur * 1e-4}


def _monetaire(tab: pd.DataFrame, jours: int) -> bool:
    """Vrai si valo_ligne a actualisé un flux unique en convention monétaire (1 + y n/360)."""
    if len(tab) != 1:
        return False
    y = tab["taux_de_valorisation"].iloc[0] / 100
    facteur = tab["flux_actualise"].iloc[0] / tab["flux"].iloc[0]
    return abs(facteur - 1 / (1 + y * jours / 360)) < 1e-10


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def _valoriser_vl(p: dict, tv: float, prime: float) -> tuple[float, pd.DataFrame]:
    if p.get("echeancier"):
        return _valoriser_echeancier_taux(p, tv, prime)
    if p.get("premier_coupon"):
        return _valoriser_premier_coupon(p, tv, prime)
    if p.get("rev_monetaire"):
        return _valoriser_rev_monetaire(p, tv, prime)
    return tc.valo_ligne(p["nominal"], p["em"], p["jouis"], p["ech"], p["valo"], p["tf"], tv,
                         prime_risque=prime, echeancier=True,
                         date_revision=p["revisions"] if p["type"] == "revisable" and p["revisions"] else None)


def _valoriser_zc(p: dict, df: pd.DataFrame, prime: float) -> tuple[float, pd.DataFrame]:
    if p.get("echeancier"):
        return _valoriser_echeancier_zc(p, df, prime)
    return tc.valo_ligne_zc(df, p["nominal"], p["jouis"], p["ech"], p["valo"], p["tf"], freq=p["freq"],
                            amortissement=p["amortissement"], montant_amort=p["montant"], prime_risque=prime,
                            delai_grace=p.get("grace"))


def _valoriser_taux_unique(tab_zc: pd.DataFrame, y_pct: float, prime: float) -> tuple[float, pd.DataFrame]:
    """Amortissable au taux négocié : mêmes flux, actualisés à un taux actuariel unique."""
    y = tc._arrondir(tc._arrondir(y_pct / 100 + prime / 10000, 8), 5)
    tab = tab_zc.copy()
    tab["taux_de_valorisation"] = y * 100
    tab["flux_actualise"] = tab["flux"] / (1 + y) ** tab["maturite_residuelle_en_annee"]
    return float(tab["flux_actualise"].sum()), tab


BASES_360 = ("364/360", "365/360", "366/360")
BASES_COUPON = {"R/R": "Exact/exact", "360": "Exact/360"}


def facteur_coupon(p: dict) -> float:
    """
    Coupon de la période en cours en base exact/360 (METHODE_COUPON 364/360, 365/360, 366/360) :
    coupon = N × taux × jours réels de la période / 360. La première période (qui commence à la
    jouissance) reste en exact/exact : le taux facial d'émission est actuariel, le taux révisé
    (monétaire + spread) s'applique en exact/360 à partir de la première révision.
    Renvoie le facteur multiplicatif à appliquer au taux facial (1 si exact/exact).
    """
    if p.get("base_coupon") != "360":
        return 1.0
    f = {"annuel": 1, "semestriel": 2, "trimestriel": 4}.get(p.get("freq", "annuel"), 1)
    if p["type"] in ("in_fine", "revisable") and f != 1 and not p.get("rev_amorti"):
        f = 1                                            # valo_ligne : coupons annuels
    debut = tc._coup_pcd(p["valo"], p["ech"], f)
    if debut <= p["jouis"]:
        return 1.0
    fin = tc._coup_ncd(p["valo"], p["ech"], f)
    return (fin - debut).days * f / 360


def premier_coupon(p: dict) -> date | None:
    """
    In fine dont la jouissance ne tombe pas sur une date anniversaire de l'échéance (ex. jouissance
    03/04/2026, échéance 05/04/2027) : le premier coupon n'est pas un petit coupon de quelques jours
    sur la grille de l'échéance, il court de la jouissance jusqu'à la date de coupon la plus proche
    de jouissance + 1 an (premier coupon long ou court). Renvoie cette date, ou None si la ligne est
    régulière (jouissance sur la grille) ou monétaire (maturité initiale ≤ 1 an).
    """
    j, e = p.get("jouis"), p.get("ech")
    if p.get("type") != "in_fine" or not (j and e) or p.get("rev_amorti"):
        return None
    if (e - j).days <= 365 + tc.bissextile(e) or tc._coup_pcd(j, e) == j:
        return None
    try:
        cible = j.replace(year=j.year + 1)
    except ValueError:                                   # 29/02
        cible = j.replace(year=j.year + 1, day=28)
    grille = [tc._date_coupon(e, k) for k in range(tc._coup_num(j, e))]
    # Date la plus proche de jouissance + 1 an ; à égalité, la plus lointaine (premier coupon long)
    return min(grille, key=lambda d: (abs((d - cible).days), -d.toordinal()))


def _base_premier(p: dict, fc: date) -> int:
    """Base du premier coupon : 360 si METHODE_COUPON en x/360, sinon 365 (366 si l'année du coupon est bissextile)."""
    return 360 if p.get("base_coupon") == "360" else 365 + tc.bissextile(fc)


def _valoriser_premier_coupon(p: dict, tv: float, prime: float) -> tuple[float, pd.DataFrame]:
    """
    In fine avant son premier coupon irrégulier (voir premier_coupon) :
    premier coupon = N × taux × (date du 1er coupon − jouissance) / base ; coupons suivants pleins
    (N × taux × jours / 360 en base x/360). Résiduelle ≤ 1 an : flux unique actualisé en monétaire
    1 / (1 + y × jours / 360) ; sinon actualisation actuarielle comme ValoLigne.
    """
    n, valo, ech, fc = p["nominal"], p["valo"], p["ech"], p["premier_coupon"]
    tf = p["tf"] / 100
    y = tc._arrondir(tc._arrondir(tv / 100 + prime / 10000, 8), 5)
    cf = p.get("coupon_fixe")
    flux = [(fc, n * cf if cf else n * tf * (fc - p["jouis"]).days / _base_premier(p, fc))]
    prec = fc
    for d in sorted(tc._date_coupon(ech, k) for k in range(tc._coup_num(fc, ech))):
        flux.append((d, n * cf if cf else n * tf * ((d - prec).days / 360 if p.get("base_coupon") == "360" else 1.0)))
        prec = d
    flux[-1] = (ech, flux[-1][1] + n)
    matres = (ech - valo).days
    a = 365 + tc.bissextile(tc._coup_ncd(valo, ech))
    lignes = []
    if matres <= 365:
        montant = sum(c for _, c in flux)
        lignes.append((ech, matres / a, montant, montant / (1 + y * matres / 360)))
    else:
        e0 = (fc - valo).days / a
        for k, (d, c) in enumerate(flux):
            lignes.append((d, e0 + k, c, c * (1 + y) ** -(e0 + k)))
    tab = pd.DataFrame(lignes, columns=["date_de_reglement", "maturite_residuelle_en_annee", "flux", "flux_actualise"])
    tab["date_de_reglement"] = pd.to_datetime(tab["date_de_reglement"])
    tab.insert(2, "taux_de_valorisation", y * 100)
    return float(tab["flux_actualise"].sum()), tab


# ---------------------------------------------------------------------------
# Échéancier imposé (ECHEANCIERS.xlsm) : flux lus, pas reconstitués
# ---------------------------------------------------------------------------

def _mois(d: date, origine: date) -> int:
    return (d.year - origine.year) * 12 + d.month - origine.month


def _tombees(p: dict) -> tuple[list[tuple], list[tuple]]:
    """(tombées passées, tombées à venir) de l'échéancier imposé, ramenées au nominal saisi."""
    k = p["nominal"] / (p.get("nominal_ref") or p["nominal"])
    lignes = [(d, a * k, r * k, c * k) for d, a, r, c in p["echeancier"]]
    return [l for l in lignes if l[0] <= p["valo"]], [l for l in lignes if l[0] > p["valo"]]


def _fin_echeancier(p: dict) -> date:
    """Révisable : prochaine révision (ou échéance) ; sinon dernière tombée."""
    if p["type"] == "revisable":
        return p["revisions"][0] if p.get("revisions") else p["ech"]
    return p["echeancier"][-1][0]


def _flux_echeancier(p: dict) -> list[tuple[date, float]]:
    """Flux à venir (coupon + amortissement) ; révisable : jusqu'à la révision, capital restant dû remboursé à la révision."""
    passees, futures = _tombees(p)
    if p["type"] != "revisable":
        return [(d, c + a) for d, a, _, c in futures if c + a]
    fin = _fin_echeancier(p)
    avant = [l for l in futures if l[0] <= fin]
    crd = avant[-1][2] if avant else (passees[-1][2] if passees else p["nominal"])
    flux = [(d, c + a) for d, a, _, c in avant]
    if flux and flux[-1][0] == fin:
        flux[-1] = (fin, flux[-1][1] + crd)
    else:
        flux.append((fin, crd))
    return [f for f in flux if f[1]]


def _temps(flux: list[tuple[date, float]], valo: date) -> list[float]:
    """Maturité de chaque flux : premier flux en jours / 365 (366), puis + mois écoulés / 12 (comme ValoLigne / ZC)."""
    d1 = flux[0][0]
    t0 = (d1 - valo).days / (365 + tc.bissextile(d1))
    return [t0 + _mois(d, d1) / 12 for d, _ in flux]


def _valoriser_echeancier_taux(p: dict, tv: float, prime: float) -> tuple[float, pd.DataFrame]:
    """
    In fine / révisable à échéancier imposé, actualisé au taux unique `tv` (%) de la maturité retenue :
    échéance (in fine) ou prochaine révision (révisable). Maturité ≤ 1 an : VA = flux / (1 + y × jours / 360),
    taux non arrondi (règle du révisable monétaire) ; sinon actuariel, taux ROUND(tv + prime ; 5).
    """
    valo, flux = p["valo"], _flux_echeancier(p)
    jours = (_fin_echeancier(p) - valo).days
    lignes = []
    if jours <= 365:
        y = (tv + prime / 100) / 100
        for d, m in flux:
            j = (d - valo).days
            lignes.append((d, j / 365, y * 100, m, m / (1 + y * j / 360)))
    else:
        y = tc._arrondir(tc._arrondir(tv / 100 + prime / 10000, 8), 5)
        for (d, m), t in zip(flux, _temps(flux, valo)):
            lignes.append((d, t, y * 100, m, m / (1 + y) ** t))
    tab = pd.DataFrame(lignes, columns=["date_de_reglement", "maturite_residuelle_en_annee", "taux_de_valorisation",
                                        "flux", "flux_actualise"])
    tab["date_de_reglement"] = pd.to_datetime(tab["date_de_reglement"])
    return float(tab["flux_actualise"].sum()), tab


def _valoriser_echeancier_zc(p: dict, df: pd.DataFrame, prime: float) -> tuple[float, pd.DataFrame]:
    """Amortissable à échéancier imposé : chaque flux au ZC de sa maturité, taux ROUND(ZC + prime ; 5)."""
    valo, flux = p["valo"], _flux_echeancier(p)
    d0 = pd.Timestamp(valo).to_pydatetime()
    zz = tc._zz_bootstrap(df, d0)
    lignes = []
    for (d, m), t in zip(flux, _temps(flux, valo)):
        y = tc._arrondir(tc._arrondir(tc._zcp_depuis_zz(df, zz, t, d0, 365) + prime / 10000, 8), 5)
        lignes.append((d, t, y * 100, m, m / (1 + y) ** t))
    tab = pd.DataFrame(lignes, columns=["date_de_reglement", "maturite_residuelle_en_annee", "taux_de_valorisation",
                                        "flux", "flux_actualise"])
    tab["date_de_reglement"] = pd.to_datetime(tab["date_de_reglement"])
    return float(tab["flux_actualise"].sum()), tab


def situation_echeancier(p: dict) -> dict:
    """
    Coupon couru, capital restant dû, amortissements restants : coupon couru = prochain coupon × jours
    écoulés depuis la tombée de coupon précédente (ou la jouissance) / jours de la période.
    """
    valo = p["valo"]
    passees, futures = _tombees(p)
    crd = passees[-1][2] if passees else p["nominal"]
    prochain = next((l for l in futures if l[3]), None)
    depart = next((l[0] for l in reversed(passees) if l[3]), None) or p["jouis"] or p["em"]
    cc = 0.0
    if prochain and depart and prochain[0] > depart:
        cc = prochain[3] * max(0, (valo - depart).days) / (prochain[0] - depart).days
    un_an = [l[3] for l in futures if (l[0] - valo).days <= 365]
    return {"cc": cc, "depart": depart, "crd": crd, "prochaine": prochain[0] if prochain else None,
            "amorts_restants": [(d, a) for d, a, _, _ in futures if a > 0], "coupon_annuel": sum(un_an)}


def _preparer(p: dict) -> dict:
    """Base du coupon ; révisables : révisions à venir, et capital restant dû si le capital s'amortit."""
    valo = p["valo"]
    if p.get("echeancier"):
        # Échéancier imposé : flux lus tels quels ; révisable : seules les révisions à venir comptent
        if p["type"] == "revisable":
            futures = sorted(d for d in (p.get("revisions") or []) if valo < d < p["ech"])
            p = {**p, "revisions": futures or None}
        return {**p, "rev_monetaire": False}
    fc = premier_coupon(p)
    if fc and valo < fc:
        # Premier coupon irrégulier encore à venir : prorata des jours réels, pas de facteur de base
        return {**p, "premier_coupon": fc}
    if p.get("coupon_fixe") and p["type"] == "in_fine":
        # Coupon imposé : montant fixe par période, sans facteur de base
        return {**p, "tf_facial": p["tf"], "tf": p["coupon_fixe"] * 100}
    if p["type"] == "revisable":
        # Révisions encore à venir avant l'échéance ; s'il n'y en a plus, l'échéance fait office de révision
        futures = sorted(d for d in (p.get("revisions") or []) if valo < d < p["ech"])
        p = {**p, "revisions": futures or None}
        prochaine = futures[0] if futures else p["ech"]
        p["rev_monetaire"] = (prochaine - valo).days <= 365
        if p.get("rev_amorti"):
            # Révisable amortissable : in fine jusqu'à la prochaine révision, sur le capital restant dû
            sch = echeancier_amortissable(p["nominal"], p["jouis"], p["ech"], valo, p["tf"], p["freq"],
                                          p["amortissement"], p["montant"], p.get("grace"))
            p = {**p, "nominal": sch["crd"]}
    if not p.get("rev_monetaire"):
        k = facteur_coupon(p)
        if k != 1.0:
            p = {**p, "tf_facial": p["tf"], "tf": p["tf"] * k}
    return p


FREQ_N = {"annuel": 1, "semestriel": 2, "trimestriel": 4}


def _coupons_revisable(p: dict, fin: date) -> tuple[list, float, date]:
    """
    Coupons d'un révisable jusqu'à la prochaine révision `fin` : liste (date, montant), coupon couru et
    début de la période en cours. Coupon en exact/360 (N × taux × jours / 360) si METHODE_COUPON est en
    x/360 ou si le coupon est infra-annuel ; sinon exact/exact (N × taux / fréquence).
    """
    f = FREQ_N.get(p.get("freq", "annuel"), 1)
    n, tf, valo = p["nominal"], p["tf"] / 100, p["valo"]
    base_360 = p.get("base_coupon") == "360" or f > 1
    nb = tc._coup_num(valo, p["ech"], f)
    dates = [tc._date_coupon(p["ech"], k, f) for k in range(nb - 1, -1, -1)]
    debut = max(tc._coup_pcd(valo, p["ech"], f), p["jouis"])
    flux, prec = [], debut
    for d in dates:
        if d <= valo:
            continue
        if d > fin:
            break
        flux.append((d, n * tf * (d - prec).days / 360 if base_360 else n * tf / f))
        prec = d
    if not flux or flux[-1][0] < fin:
        flux.append((fin, n * tf * (fin - prec).days / 360 if base_360 else n * tf / f * (fin - prec).days
                     / max((tc._coup_ncd(prec, p["ech"], f) - prec).days, 1)))
    periode = (flux[0][0] - debut).days
    cc = flux[0][1] * (valo - debut).days / periode if periode > 0 else 0.0
    return flux, cc, debut


def _valoriser_rev_monetaire(p: dict, tv: float, prime: float) -> tuple[float, pd.DataFrame]:
    """
    Révisable dont la prochaine révision est à moins d'un an : flux (coupons de la période et nominal)
    jusqu'à la révision, actualisés au taux monétaire de la maturité de révision + prime :
    VA = flux / (1 + y × jours / 360).
    """
    valo = p["valo"]
    fin = p["revisions"][0] if p.get("revisions") else p["ech"]
    flux, _, _ = _coupons_revisable(p, fin)
    y = (tv + prime / 100) / 100
    lignes = []
    for d, c in flux:
        montant = c + (p["nominal"] if d == fin else 0.0)
        j = (d - valo).days
        lignes.append({"date_de_reglement": pd.Timestamp(d), "maturite_residuelle_en_annee": j / 365,
                       "taux_de_valorisation": round(tv + prime / 100, 5), "flux": montant,
                       "flux_actualise": montant / (1 + y * j / 360)})
    tab = pd.DataFrame(lignes)
    return float(tab["flux_actualise"].sum()), tab


def coupon_couru(p: dict, ech_eff: date) -> tuple[float, date]:
    """Coupon couru : échéancier imposé, premier coupon irrégulier, règle du révisable monétaire, sinon ValoLigne."""
    if p.get("echeancier"):
        s = situation_echeancier(p)
        return s["cc"], s["depart"]
    if p.get("premier_coupon"):
        fc, j = p["premier_coupon"], p["jouis"]
        ecoule = max(0, (p["valo"] - j).days)
        if p.get("coupon_fixe"):                         # coupon imposé : couru au prorata de la période
            return p["nominal"] * p["coupon_fixe"] * ecoule / max((fc - j).days, 1), j
        return p["nominal"] * p["tf"] / 100 * ecoule / _base_premier(p, fc), j
    if p.get("rev_monetaire"):
        _, cc, debut = _coupons_revisable(p, ech_eff)
        return cc, debut
    return coupon_couru_vl(p["nominal"], p["em"], p["jouis"], ech_eff, p["valo"], p["tf"])


def valoriser(p: dict, courbe: pd.DataFrame) -> dict:
    """
    Valorisation légère sur la courbe BKAM (sans autres sources ni chocs) : prix plein, coupon couru,
    sensibilité et duration calculées sur l'échéancier, taux de valorisation. Mêmes règles que pricer().
    """
    valo = p["valo"]
    if p.get("valo_zero"):
        # Règle de gestion : ligne toujours valorisée à 0 (ex. MA0000094351)
        vide = pd.DataFrame(columns=["date_de_reglement", "maturite_residuelle_en_annee", "taux_de_valorisation",
                                     "flux", "flux_actualise"])
        return {"valeur": 0.0, "cc": 0.0, "crd": 0.0, "ppc_pct": np.nan, "taux": np.nan, "ech_eff": p["ech"],
                "jours": (p["ech"] - valo).days, "tab": vide, "monetaire": False, "tf_coupon": p["tf"],
                "rev_monetaire": False, "valo_zero": True, "duration": 0.0, "sensibilite": 0.0, "convexite": 0.0,
                "tri": np.nan, "dv01": 0.0}
    p = _preparer(p)
    n = p["nominal"]
    if p["type"] in ("in_fine", "revisable"):
        ech_eff = echeance_effective(p["ech"], valo, p["revisions"] if p["type"] == "revisable" else None)
        jours = (ech_eff - valo).days
        taux = taux_interpole(courbe, jours, valo)
        valeur, tab = _valoriser_vl(p, taux, p["prime"])
        cc, _ = coupon_couru(p, ech_eff)
        crd, taux_aff = (situation_echeancier(p)["crd"] if p.get("echeancier") else n), taux
    else:
        ech_eff, jours = p["ech"], (p["ech"] - valo).days
        valeur, tab = _valoriser_zc(p, courbe, p["prime"])
        sch = situation_echeancier(p) if p.get("echeancier") else \
            echeancier_amortissable(n, p["jouis"], p["ech"], valo, p["tf"], p["freq"],
                                    p["amortissement"], p["montant"], p.get("grace"))
        cc, crd = sch["cc"], sch["crd"]
        taux_aff = _tri(tab["maturite_residuelle_en_annee"].to_numpy(float), tab["flux"].to_numpy(float), valeur)
    mono = p["type"] != "amortissable" and _monetaire(tab, jours)
    a = analyser(tab, valeur, mono, jours if mono else None)
    return {"valeur": valeur, "cc": cc, "crd": crd, "ppc_pct": (valeur - cc) / crd * 100 if crd else np.nan,
            "taux": taux_aff, "ech_eff": ech_eff, "jours": jours, "tab": tab, "monetaire": mono,
            "tf_coupon": p["tf"], "rev_monetaire": bool(p.get("rev_monetaire")), **a}


def pricer(p: dict, courbe: pd.DataFrame, tenors: dict, source: str, taux_negocie: float | None) -> dict:
    """
    p : type, nominal, em, jouis, ech, valo (dates), tf (%), prime (pb), revisions (liste),
        freq, amortissement, montant, grace (délai de grâce en années, facultatif),
        base_coupon ("R/R" exact/exact ou "360" exact/360) ; rev_amorti (révisable dont le capital s'amortit : valorisé
        en in fine jusqu'à la prochaine révision sur le capital restant dû).
    courbe : points BKAM ; tenors : {ténor: taux %} éventuellement renégociés.
    """
    valo, n = p["valo"], p["nominal"]
    df_t = df_tenors(tenors)
    prime = p["prime"]
    res = {"type": p["type"], "source": source}

    p = _preparer(p)
    n = p["nominal"]

    if p["type"] in ("in_fine", "revisable"):
        ech_eff = echeance_effective(p["ech"], valo, p["revisions"] if p["type"] == "revisable" else None)
        jours = (ech_eff - valo).days
        t_inf, t_sup = tenors_encadrants(jours)
        taux = {"courbe": taux_interpole(courbe, jours, valo), "tenors": taux_interpole(df_t, jours, valo)}
        taux["negocie"] = float(taux_negocie) if taux_negocie not in (None, "") else taux["courbe"]
        valos = {s: _valoriser_vl(p, taux[s], prime) for s in SOURCES}
        cc, depart = coupon_couru(p, ech_eff)
        base_ppc = n
        coupon_annuel = n * p["tf"] / 100
        if p.get("echeancier"):
            s_e = situation_echeancier(p)
            base_ppc, coupon_annuel = s_e["crd"], s_e["coupon_annuel"]
        res.update(ech_eff=ech_eff, jours=jours, encadrants=(t_inf, t_sup), wal=None, crd=base_ppc)

        def reprix(delta_pb):
            return _valoriser_vl(p, taux[source] + delta_pb / 100, prime)[0]
    else:
        valos = {"courbe": _valoriser_zc(p, courbe, prime), "tenors": _valoriser_zc(p, df_t, prime)}
        tab_flux = valos["courbe"][1]
        tri_courbe = _tri(tab_flux["maturite_residuelle_en_annee"].to_numpy(float),
                          tab_flux["flux"].to_numpy(float), valos["courbe"][0])
        taux = {"courbe": tri_courbe,
                "tenors": _tri(valos["tenors"][1]["maturite_residuelle_en_annee"].to_numpy(float),
                               valos["tenors"][1]["flux"].to_numpy(float), valos["tenors"][0])}
        y_neg = float(taux_negocie) if taux_negocie not in (None, "") else round(tri_courbe - prime / 100, 3)
        valos["negocie"] = _valoriser_taux_unique(tab_flux, y_neg, prime)
        taux["negocie"] = y_neg
        sch = situation_echeancier(p) if p.get("echeancier") else \
            echeancier_amortissable(n, p["jouis"], p["ech"], valo, p["tf"], p["freq"],
                                    p["amortissement"], p["montant"], p.get("grace"))
        cc, depart, base_ppc, coupon_annuel = sch["cc"], sch["depart"], sch["crd"], sch["coupon_annuel"]
        t_am = np.array([(d - valo).days / 365.25 for d, _ in sch["amorts_restants"]])
        a_am = np.array([a for _, a in sch["amorts_restants"]])
        jours = (p["ech"] - valo).days
        res.update(ech_eff=p["ech"], jours=jours, encadrants=None, crd=sch["crd"],
                   wal=float((t_am * a_am).sum() / a_am.sum()) if a_am.sum() else np.nan)

        def reprix(delta_pb):
            if source == "negocie":
                return _valoriser_taux_unique(tab_flux, y_neg, prime + delta_pb)[0]
            return _valoriser_zc(p, courbe if source == "courbe" else df_t, prime + delta_pb)[0]

    valeur, tab = valos[source]
    mono = p["type"] != "amortissable" and _monetaire(tab, res["jours"])
    a = analyser(tab, valeur, mono, res["jours"] if mono else None)

    # Sensibilité effective (re-pricing à ± 1 pb), pour contrôle
    pu, pd_ = reprix(1), reprix(-1)
    s_eff = (pd_ - pu) / (2 * valeur * 1e-4)

    ppc = (valeur - cc) / base_ppc
    chocs = []
    for d in CHOCS_PB:
        pc = reprix(d)
        approx = -a["sensibilite"] * d / 1e4 + 0.5 * a["convexite"] * (d / 1e4) ** 2
        chocs.append({"Choc (pb)": d, "Prix": pc, "Variation (MAD)": pc - valeur,
                      "Variation (%)": (pc / valeur - 1) * 100, "Approximation S + C (%)": approx * 100,
                      "Écart d'approximation (pb de prix)": ((pc / valeur - 1) - approx) * 1e4})

    comparaison = []
    for s in SOURCES:
        v, tb = valos[s]
        comparaison.append({"Source": SOURCES[s], "code": s, "Taux (%)": taux[s], "Prix": v,
                            "PPC (%)": (v - cc) / base_ppc * 100,
                            "Écart taux vs courbe (pb)": (taux[s] - taux["courbe"]) * 100,
                            "Écart prix vs courbe (MAD)": v - valos["courbe"][0]})

    tri = a["tri"]
    res.update(
        valeur=valeur, tab=tab, taux=taux, taux_utilise=taux[source], cc=cc, depart_cc=depart,
        ppc=ppc, ppc_pct=ppc * 100, prix_pct=valeur / base_ppc * 100, sensibilite_effective=s_eff,
        chocs=pd.DataFrame(chocs), comparaison=pd.DataFrame(comparaison),
        rendement_courant=coupon_annuel / (valeur - cc) * 100 if valeur > cc else np.nan,
        prime_decote=(ppc - 1) * 100,
        ecart_facial=(p["tf"] - taux[source]) * 100,
        point_mort_3m=(tri / 100 * 0.25) / a["sensibilite"] * 1e4 if a["sensibilite"] else np.nan,
        nb_flux=len(tab), prochain_flux=tab["date_de_reglement"].iloc[0] if len(tab) else None,
        monetaire=mono, **a,
    )
    return res
