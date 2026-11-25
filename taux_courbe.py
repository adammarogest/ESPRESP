"""
Taux à partir de la courbe BAM — traduction Python de TauxCourbe (VBA).

Entrée : DataFrame produit par get_bkam_taux_reference (bkam_taux_reference.py),
colonnes 'taux_moyen_pondere' (en %) et 'maturite_residuelle' (en jours).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_datetime(v) -> datetime:
    """Normalise date / datetime / pd.Timestamp / 'DD/MM/YYYY' en datetime."""
    if isinstance(v, datetime):          # datetime et pd.Timestamp
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    if isinstance(v, str):
        return datetime.strptime(v.strip(), "%d/%m/%Y")
    raise TypeError(f"Type de date non supporté : {type(v)!r}")


def _interpoler(y1: float, y2: float, x1: float, x2: float, x: float) -> float:
    """Interpolation / extrapolation linéaire (interpoler en VBA)."""
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1)


def _arrondir(x: float, decimales: int = 3) -> float:
    """Arrondi 'Excel' (half-up), contrairement à round() qui arrondit au pair."""
    q = Decimal(1).scaleb(-decimales)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Fonctions auxiliaires
# ---------------------------------------------------------------------------

def bissextile(date_cp) -> int:
    """
    Bissextile(DateCP) : 1 si un 29 février tombe entre la même date un an
    avant et DateCP (bornes incluses), ou si DateCP est un 29 février ; 0 sinon.
    """
    d = _as_datetime(date_cp).date()
    if d.month == 2 and d.day == 29:
        return 1
    debut = d.replace(year=d.year - 1)   # sûr : d n'est pas un 29/02
    for annee in (d.year - 1, d.year):
        if calendar.isleap(annee) and debut <= date(annee, 2, 29) <= d:
            return 1
    return 0


def tm_ta_valo(tm: float, n: float, c_prochain) -> float:
    """Taux monétaire -> taux actuariel (Tm_TaValo). Taux en décimal."""
    a = bissextile(c_prochain) + 365
    return (1 + tm * n / 360) ** (a / n) - 1


def ta_tm_valo(ta: float, n: float, c_prochain) -> float:
    """Taux actuariel -> taux monétaire (Ta_TmValo). Taux en décimal."""
    a = bissextile(c_prochain) + 365
    return ((1 + ta) ** (n / a) - 1) * (360 / n)


def mat_min_courbe(courbe: pd.DataFrame) -> int:
    """MatMinCourbe : maturité de la 1re ligne de la courbe (triée)."""
    return int(courbe["maturite_residuelle"].iloc[0])


def seuil_un_an(datevl) -> int:
    """
    Frontière monétaire / actuariel en jours : 365 + Bissextile(datevl + 1 an),
    soit 366 si un 29 février tombe dans l'année qui suit datevl, 365 sinon.
    """
    d0 = _as_datetime(datevl).date()
    try:
        d1 = d0.replace(year=d0.year + 1)
    except ValueError:                   # datevl = 29/02
        d1 = d0.replace(year=d0.year + 1, day=28)
    return 365 + bissextile(d1)


# ---------------------------------------------------------------------------
# TauxCourbe
# ---------------------------------------------------------------------------

def taux_courbe_brut(df: pd.DataFrame, matur: float, datevl, ech=None) -> float:
    """
    Cœur de TauxCourbe : taux en DÉCIMAL, non arrondi (comme la fonction VBA).
    Utilisé en interne par zcp().
    Convention : monétaire si matur <= 1 an, actuariel au-delà.
    """
    if ech:
        matur = (_as_datetime(ech) - datetime.now()).total_seconds() / 86400

    courbe = df.sort_values("maturite_residuelle").reset_index(drop=True)
    mats = courbe["maturite_residuelle"].astype(float).to_numpy()
    taux = courbe["taux_moyen_pondere"].astype(float).to_numpy() / 100   # % -> décimal
    last = len(courbe) - 1
    a = seuil_un_an(datevl)

    # Maturité <= 1er point de la courbe
    if matur <= mat_min_courbe(courbe):
        return taux[0]

    # Au-delà du dernier point : extrapolation sur les 2 derniers
    if matur >= mats[last]:
        return _interpoler(taux[last - 1], taux[last],
                           mats[last - 1], mats[last], matur)

    # À l'intérieur de la courbe
    for i in range(last):
        matinf, matsup = mats[i], mats[i + 1]

        if matinf <= matur < matsup:
            taux1, taux2 = taux[i], taux[i + 1]

            if matur <= a < matsup:        # cible monétaire, point sup actuariel
                taux2 = ta_tm_valo(taux[i + 1], matsup,
                                   _as_datetime(datevl) + timedelta(days=matsup))
            elif matinf <= a < matur:      # cible actuarielle, point inf monétaire
                taux1 = tm_ta_valo(taux[i], matinf,
                                   _as_datetime(datevl) + timedelta(days=matinf))

            return _interpoler(taux1, taux2, matinf, matsup, matur)

    raise ValueError(f"Aucun intervalle de la courbe ne contient la maturité {matur}")


def taux_courbe(df: pd.DataFrame, matur: float, datevl, ech=None,
                decimales: int = 3) -> float:
    """
    Traduction de TauxCourbe.

    df        : DataFrame de get_bkam_taux_reference (taux en %)
    matur     : maturité en jours (ignorée si `ech` est fourni)
    datevl    : date de valeur (date, datetime, pd.Timestamp ou 'JJ/MM/AAAA')
    ech       : date d'échéance optionnelle (mêmes formats)
    decimales : décimales du résultat, en %

    Retourne le taux en %, arrondi (ex. 2.345).
    """
    return _arrondir(taux_courbe_brut(df, matur, datevl, ech) * 100, decimales)


# ---------------------------------------------------------------------------
# ZCP — taux zéro coupon
# ---------------------------------------------------------------------------

def _edate_jours(datevl, mois: int) -> int:
    """Équivalent de EDate(datevl, mois) - datevl, en jours."""
    d0 = pd.Timestamp(_as_datetime(datevl)).normalize()
    return int((d0 + pd.DateOffset(months=mois) - d0).days)


def taux_courbe_zc(df: pd.DataFrame, matur: float, datevl) -> float:
    """
    Traduction de TauxCourbeZC : taux zéro coupon (actuariel) pour une maturité
    inférieure à 365 jours.

    Les taux monétaires de la courbe sont d'abord convertis en actuariel
    (Tm_TaValo), puis interpolés linéairement.

    df     : DataFrame de get_bkam_taux_reference (taux en %)
    matur  : maturité en jours
    datevl : date de valeur

    Retourne le taux en DÉCIMAL, non arrondi (comme le VBA).
    """
    d0 = _as_datetime(datevl)

    courbe = df.sort_values("maturite_residuelle").reset_index(drop=True)
    mats = courbe["maturite_residuelle"].astype(float).to_numpy()
    taux = courbe["taux_moyen_pondere"].astype(float).to_numpy() / 100   # % -> décimal
    last = len(courbe) - 1

    def _ta(t: float, n: float) -> float:
        """Tm_TaValo(t, n, datevl + n)."""
        return tm_ta_valo(t, n, d0 + timedelta(days=n))

    # Maturité <= 1er point de la courbe : entre 1 jour et le 1er point,
    # avec le taux du 1er point converti aux deux bornes
    if matur <= mat_min_courbe(courbe):
        matinf, matsup = 1.0, mats[0]
        taux1, taux2 = _ta(taux[0], matinf), _ta(taux[0], matsup)
        return _interpoler(taux1, taux2, matinf, matsup, matur)

    # À l'intérieur de la courbe
    for i in range(last):
        matinf, matsup = mats[i], mats[i + 1]

        if matinf <= matur < matsup:
            taux1 = _ta(taux[i], matinf)
            taux2 = _ta(taux[i + 1], matsup)

            # Point sup au-delà d'un an : on le remplace par le taux à 364 jours
            if matinf < 365 <= matsup:
                matsup = 364.0
                taux2 = _ta(taux_courbe_brut(df, matsup, d0), matsup)

            return _interpoler(taux1, taux2, matinf, matsup, matur)

    raise ValueError(f"Aucun intervalle de la courbe ne contient la maturité {matur}")


def zcp(df: pd.DataFrame, mr: float, datevl, a: float = 365,
        decimales: int | None = 3) -> float:
    """
    Traduction de ZCP : taux zéro coupon issu de la courbe BAM.

    df        : DataFrame de get_bkam_taux_reference (taux en %)
    mr        : maturité — ATTENTION aux unités, comme dans le VBA :
                  1, 91, 182, 364 -> ténors courts, en JOURS
                  (mr = 1 est cependant écrasé par le ténor 1 an, cf. VBA)
                  autres valeurs  -> en ANNÉES (entier = ténor 1..30 ans,
                  non entier = interpolation linéaire, < 1 = TauxCourbeZC)
    datevl    : date de valeur
    a         : base annuelle (paramètre A du VBA)
    decimales : décimales du résultat en % (None = pas d'arrondi)

    Retourne le taux ZC en %.
    """
    d0 = _as_datetime(datevl)
    zz = _zz_bootstrap(df, d0)
    res = _zcp_depuis_zz(df, zz, mr, d0, a) * 100           # décimal -> %
    return res if decimales is None else _arrondir(res, decimales)


def _zz_bootstrap(df: pd.DataFrame, d0: datetime) -> list[float]:
    """Tableaux tt / zz de ZCP : ZC des ténors (indices 1..34 comme en VBA), en décimal."""
    tt = [0.0] * 35
    tt[1] = taux_courbe_brut(df, 1, d0)
    tt[2] = taux_courbe_brut(df, 91, d0)
    tt[3] = taux_courbe_brut(df, 182, d0)
    tt[4] = taux_courbe_brut(df, 364, d0)
    for i in range(5, 35):
        tt[i] = taux_courbe_brut(df, _edate_jours(d0, 12 * (i - 4)), d0)

    zz = [0.0] * 35
    for k, n in ((1, 1), (2, 91), (3, 182), (4, 364), (5, 366)):
        zz[k] = tm_ta_valo(tt[k], n, d0 + timedelta(days=n))

    # Bootstrap : obligations au pair, coupon annuel tt[j]
    for j in range(6, 35):
        temp1 = -sum(tt[j] / (1 + zz[i]) ** (i - 4) for i in range(5, j))
        zz[j] = ((1 + tt[j]) / (temp1 + 1)) ** (1 / (j - 4)) - 1
    return zz


def _zcp_depuis_zz(df: pd.DataFrame, zz: list[float], mr: float,
                   d0: datetime, a: float) -> float:
    """Sélection du ZC de ZCP à partir du bootstrap, en décimal, non arrondi.
    Les tests s'écrasent dans l'ordre du VBA."""
    res = None

    # Ténors courts (en jours)
    for m, k in ((1, 1), (91, 2), (182, 3), (364, 4)):
        if mr == m:
            res = zz[k]

    # Ténors annuels (en années)
    for i in range(5, 35):
        if mr == _arrondir(_edate_jours(d0, 12 * (i - 4)) / a, 0):
            res = zz[i]

    # Non-ténors < 1 an
    if mr < 1:
        res = taux_courbe_zc(df, _arrondir(mr * a, 0), d0)
        res = _arrondir(res, 5)

    # Non-ténors entre deux années : interpolation linéaire des ZC
    for i in range(1, 30):
        matinf = int(_edate_jours(d0, 12 * i) / a)          # RoundDown(x, 0)
        matsup = int(_edate_jours(d0, 12 * (i + 1)) / a)
        if matinf < mr < matsup:
            res = zz[i + 4] + (mr - matinf) * (zz[i + 5] - zz[i + 4]) / (matsup - matinf)

    if res is None:
        raise ValueError(f"Maturité {mr} hors du domaine de ZCP")
    return float(res)


# ---------------------------------------------------------------------------
# Dates de coupon (équivalents de COUPNCD / COUPPCD / COUPNUM)
# freq : 1 = annuel, 2 = semestriel, 4 = trimestriel
# ---------------------------------------------------------------------------

def _fin_de_mois(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _date_coupon(ech: date, k: int, freq: int = 1) -> date:
    """k-ième date de coupon avant l'échéance (k = 0 : l'échéance elle-même).
    Règle fin de mois d'Excel : échéance en fin de mois -> coupons en fin de mois."""
    mois = ech.year * 12 + (ech.month - 1) - k * 12 // freq
    annee, m = divmod(mois, 12)
    m += 1
    dernier = calendar.monthrange(annee, m)[1]
    jour = dernier if _fin_de_mois(ech) else min(ech.day, dernier)
    return date(annee, m, jour)


def _coup_num(regl: date, ech: date, freq: int = 1) -> int:
    """COUPNUM(regl, ech, freq) : nombre de coupons dans ]regl, ech]."""
    k = 0
    while _date_coupon(ech, k, freq) > regl:
        k += 1
    return k


def _coup_ncd(regl: date, ech: date, freq: int = 1) -> date:
    """COUPNCD(regl, ech, freq) : première date de coupon strictement après regl."""
    return _date_coupon(ech, _coup_num(regl, ech, freq) - 1, freq)


def _coup_pcd(regl: date, ech: date, freq: int = 1) -> date:
    """COUPPCD(regl, ech, freq) : dernière date de coupon <= regl."""
    return _date_coupon(ech, _coup_num(regl, ech, freq), freq)


# ---------------------------------------------------------------------------
# ValoLigne — valorisation d'une ligne obligataire
# ---------------------------------------------------------------------------

def _prochaine_revision(date_revision, valo: date) -> date:
    """Première date de révision strictement après valo (date seule ou liste)."""
    if isinstance(date_revision, (list, tuple, set, pd.Series, pd.Index)):
        candidates = sorted(_as_datetime(d).date() for d in date_revision)
    else:
        candidates = [_as_datetime(date_revision).date()]
    futures = [d for d in candidates if d > valo]
    if not futures:
        raise ValueError("Aucune date de révision postérieure à la date de valorisation")
    return futures[0]


def valo_ligne(n: float, em, jouis, ech, valo, tf: float, tv: float,
               prime_risque: float = 0.0, echeancier: bool = False,
               date_revision=None):
    """
    Traduction de ValoLigne : valeur (coupon couru inclus) d'une ligne de nominal n.

    n            : nominal
    em, jouis    : dates d'émission et de jouissance
    ech, valo    : dates d'échéance et de valorisation
                   (dates : date, datetime, pd.Timestamp ou 'JJ/MM/AAAA')
    tf           : taux facial, en % (taux en vigueur jusqu'à la prochaine révision)
    tv           : taux de valorisation, en % (ex. sortie de taux_courbe ou zcp)
    prime_risque : prime de risque, en points de base
    echeancier   : si True, renvoie aussi l'échéancier des flux
    date_revision: ligne révisable (in fine) — date de révision, ou liste des
                   dates de révision. La prochaine révision après valo devient
                   la nouvelle échéance : le nominal y est remboursé et la ligne
                   est valorisée comme un in fine classique, avec les mêmes règles
                   de coupon et de maturité. Si elle tombe après l'échéance,
                   l'échéance réelle est conservée.

    Retourne la valeur, ou (valeur, DataFrame) si echeancier=True. Colonnes :
        date_de_reglement, maturite_residuelle_en_annee,
        taux_de_valorisation (%), flux, flux_actualise
    La valeur est la somme de la colonne flux_actualise.

    maturite_residuelle_en_annee = exposant d'actualisation pour les flux
    actuariels ; jours / A pour les flux monétaires (affichage seulement,
    l'actualisation monétaire se fait en 1 + tv × jours / 360).
    """
    em, jouis, ech, valo = (_as_datetime(x).date() for x in (em, jouis, ech, valo))
    if valo >= ech:
        raise ValueError("La date de valorisation doit être antérieure à l'échéance")

    # Ligne révisable : la prochaine date de révision remplace l'échéance
    if date_revision is not None:
        ech = min(ech, _prochaine_revision(date_revision, valo))

    tf = tf / 100
    tv = tv / 100 + prime_risque / 10000
    tv = _arrondir(_arrondir(tv, 8), 5)          # Round(tv, 8) puis Round(tv, 5)

    nbc = _coup_num(valo, ech)
    a = 365 + bissextile(_coup_ncd(valo, ech))
    matinit = (ech - em).days
    matres = (ech - valo).days
    cp = tf * n                                   # coupon plein

    lignes = []                                   # (date, maturité en années, flux, facteur)

    def flux_monetaire(montant: float) -> None:
        """Flux unique à l'échéance, actualisation monétaire."""
        lignes.append((ech, matres / a, montant, 1 / (1 + tv * matres / 360)))

    def flux_actuariel(d: date, expo: float, montant: float) -> None:
        lignes.append((d, expo, montant, (1 + tv) ** (-expo)))

    def coupon(i: int, dc1_index: int) -> date:
        """Date du i-ème coupon compté à partir de la date d'indice 0 (= dc1)."""
        return _date_coupon(ech, dc1_index - i)

    def coupons_reguliers() -> None:
        dc1 = _coup_ncd(valo, ech)
        e0 = (dc1 - valo).days / a
        for i in range(nbc):
            flux_actuariel(coupon(i, nbc - 1), i + e0, cp)
        flux_actuariel(ech, nbc - 1 + e0, n)

    # 1. Maturité initiale <= 1 an
    if matinit <= a:
        flux_monetaire(n * (1 + tf * matinit / 360))

    # 2. Maturité initiale >= 1 an, résiduelle <= 1 an, ligne normale
    elif matinit >= 365 and matres <= 365 and em == jouis:
        flux_monetaire(n * (1 + tf))

    # 3. Idem, ligne atypique, un seul coupon
    elif matinit >= 365 and matres <= 365 and em != jouis and _coup_num(jouis, ech) == 1:
        flux_monetaire(n * (1 + tf * matinit / a))

    # 4. Idem, ligne atypique, plusieurs coupons
    elif matinit >= 365 and matres <= 365 and em != jouis:
        if valo <= _coup_pcd(jouis, ech):
            raise ValueError("Cas non couvert par ValoLigne : date de valorisation "
                             "antérieure ou égale au coupon précédant la jouissance")
        flux_monetaire(n * (1 + tf))

    # 5. Maturité résiduelle > 1 an, ligne normale
    elif matinit >= 365 and matres > 365 and em == jouis:
        coupons_reguliers()

    # 6. Maturité résiduelle >= 1 an, ligne atypique (1er coupon brisé)
    else:
        fc = _coup_ncd(jouis + timedelta(days=1), ech)        # 1re date de coupon

        if valo < fc:
            if valo >= jouis:
                # Valorisation entre jouissance et 1er coupon
                dc1 = _coup_ncd(valo, ech)
                e0 = (dc1 - valo).days / a
                cp1 = tf * n * (fc - em).days / a             # coupon brisé
                flux_actuariel(fc, (fc - valo).days / a, cp1)
                for i in range(1, nbc):
                    flux_actuariel(coupon(i, nbc - 1), i + e0, cp)
                flux_actuariel(ech, nbc - 1 + e0, n)
            else:
                # Valorisation avant jouissance
                dcp = _coup_ncd(valo, ech)
                a = 365 + bissextile(fc)
                e0 = (dcp - valo).days / a
                cp1 = tf * n * (fc - em).days / a             # coupon brisé
                flux_actuariel(coupon(1, nbc - 1), 1 + e0, cp1)
                for i in range(2, nbc):
                    flux_actuariel(coupon(i, nbc - 1), i + e0, cp)
                flux_actuariel(ech, nbc - 1 + e0, n)
        else:
            # Valorisation après le 1er coupon : comme une ligne normale
            coupons_reguliers()

    tab = pd.DataFrame(lignes, columns=["date_de_reglement", "maturite_residuelle_en_annee",
                                        "flux", "facteur"])
    tab["flux_actualise"] = tab["flux"] * tab["facteur"]
    tab = (tab.groupby("date_de_reglement", as_index=False)
              .agg(maturite_residuelle_en_annee=("maturite_residuelle_en_annee", "first"),
                   flux=("flux", "sum"),
                   flux_actualise=("flux_actualise", "sum"))
              .sort_values("date_de_reglement")
              .reset_index(drop=True))
    tab["date_de_reglement"] = pd.to_datetime(tab["date_de_reglement"])
    tab.insert(2, "taux_de_valorisation", tv * 100)
    tab = tab[tab["flux"] != 0].reset_index(drop=True)

    valeur = float(tab["flux_actualise"].sum())
    return (valeur, tab) if echeancier else valeur


# ---------------------------------------------------------------------------
# Valorisation par la courbe zéro coupon (fréquence et amortissement au choix)
# ---------------------------------------------------------------------------

_FREQUENCES = {"annuel": 1, "semestriel": 2, "trimestriel": 4}
_AMORTISSEMENTS = ("in_fine", "lineaire", "constant")


def _fin_grace(jouis: date, delai_grace) -> date | None:
    """Date de fin du délai de grâce : jouissance + délai (en années, fraction acceptée), ou None."""
    if delai_grace in (None, "") or float(delai_grace) <= 0:
        return None
    mois = int(round(float(delai_grace) * 12))
    total = jouis.year * 12 + (jouis.month - 1) + mois
    annee, m = divmod(total, 12)
    m += 1
    return date(annee, m, min(jouis.day, calendar.monthrange(annee, m)[1]))


def _amortissements(n: float, dates: list, jouis: date, amortissement: str,
                    montant_amort: float | None = None, delai_grace=None) -> list[float]:
    """
    Amortissement du capital à chaque date de l'échéancier.
    Délai de grâce (facultatif, en années) : aucune date antérieure ou égale à jouissance + délai
    n'amortit ; le capital s'amortit sur les dates suivantes seulement. Si le délai couvre toute
    la durée, le capital est remboursé à l'échéance.
    """
    nb = len(dates)
    if amortissement == "in_fine":
        return [0.0] * (nb - 1) + [float(n)]
    fin = _fin_grace(jouis, delai_grace)
    actives = [k for k, d in enumerate(dates) if fin is None or d > fin] or [nb - 1]
    amorts = [0.0] * nb
    if amortissement == "lineaire":
        for k in actives:
            amorts[k] = n / len(actives)
    else:
        reste = float(n)
        for i, k in enumerate(actives):
            a_k = reste if i == len(actives) - 1 else min(float(montant_amort), reste)
            amorts[k] = a_k
            reste -= a_k
    return amorts


def valo_ligne_zc(df: pd.DataFrame, n: float, jouis, ech, valo, tf: float,
                  freq: str = "annuel", amortissement: str = "in_fine",
                  montant_amort: float | None = None, prime_risque: float = 0.0,
                  delai_grace: float | None = None):
    """
    Valorise une ligne en actualisant chaque flux au taux zéro coupon (zcp)
    de sa propre maturité.

    df            : courbe BAM (DataFrame de get_bkam_taux_reference, taux en %)
    n             : nominal INITIAL (avant tout amortissement)
    jouis, ech    : dates de jouissance et d'échéance
    valo          : date de valorisation
                    (dates : date, datetime, pd.Timestamp ou 'JJ/MM/AAAA')
    tf            : taux facial, en %
    freq          : 'annuel', 'semestriel' ou 'trimestriel'
    amortissement : 'in_fine'  -> tout le nominal à l'échéance
                    'lineaire' -> nominal / nombre de périodes à chaque date
                    'constant' -> `montant_amort` à chaque date, le reliquat
                                  éventuel du capital étant remboursé à l'échéance
    montant_amort : montant amorti par période (obligatoire si 'constant')
    delai_grace   : facultatif, nombre d'années après la jouissance pendant lesquelles le capital
                    ne s'amortit pas (seuls les coupons sont payés) ; l'amortissement linéaire
                    ou constant porte sur les dates postérieures à jouissance + délai.
                    Vide ou 0 : calcul normal.
    prime_risque  : prime de risque en points de base, ajoutée au ZC de chaque
                    flux (in fine comme amortissable) :
                    taux_de_valorisation = ARRONDI(ZC + prime_risque / 10000 ; 5)
                    (en décimal, arrondi « Excel » half-up comme ROUND(x, 5))

    Retourne toujours (prix, tab). Colonnes de tab :
        date_de_reglement, maturite_residuelle_en_annee,
        taux_de_valorisation (%), flux, flux_actualise
    flux = coupon (sur le capital restant dû) + amortissement.
    Maturité du 1er flux = (date du flux - valo) / (365 ou 366), puis + 1/freq.
    Le prix est la somme de flux_actualise.
    """
    jouis, ech, valo = (_as_datetime(x).date() for x in (jouis, ech, valo))
    if valo >= ech:
        raise ValueError("La date de valorisation doit être antérieure à l'échéance")
    if not jouis < ech:
        raise ValueError("La date de jouissance doit être antérieure à l'échéance")

    f = _FREQUENCES.get(str(freq).lower())
    if f is None:
        raise ValueError(f"freq doit être parmi {list(_FREQUENCES)}")
    amortissement = str(amortissement).lower()
    if amortissement not in _AMORTISSEMENTS:
        raise ValueError(f"amortissement doit être parmi {list(_AMORTISSEMENTS)}")
    if amortissement == "constant" and not montant_amort:
        raise ValueError("montant_amort est obligatoire pour un amortissement constant")

    tf = tf / 100

    # --- Échéancier complet depuis la jouissance ---------------------------
    nb = _coup_num(jouis, ech, f)
    dates = [_date_coupon(ech, nb - 1 - k, f) for k in range(nb)]   # ordre chronologique

    amorts = _amortissements(n, dates, jouis, amortissement, montant_amort, delai_grace)

    crd, coupons = float(n), []                    # capital restant dû
    for k in range(nb):
        if k == 0 and _coup_pcd(jouis, ech, f) != jouis:
            # 1er coupon brisé : jouissance hors calendrier des coupons
            base = 365 + bissextile(dates[0])
            coupons.append(crd * tf * (dates[0] - jouis).days / base)
        else:
            coupons.append(crd * tf / f)
        crd -= amorts[k]

    # --- Flux restants et actualisation au ZC -------------------------------
    k0 = next(k for k, d in enumerate(dates) if d > valo)
    base = 365 + bissextile(dates[k0])
    t0 = (dates[k0] - valo).days / base

    d0 = _as_datetime(valo)
    zz = _zz_bootstrap(df, d0)                     # bootstrap calculé une seule fois

    # Prime de risque ajoutée au ZC de chaque flux, quel que soit l'amortissement
    prime = prime_risque / 10000

    lignes = []
    for j, k in enumerate(range(k0, nb)):
        t = t0 + j / f
        zc = _zcp_depuis_zz(df, zz, t, d0, 365)
        tv = _arrondir(_arrondir(zc + prime, 8), 5)   # ROUND(ZC + prime ; 5) d'Excel (8e décimale : nettoyage du flottant)
        flux = coupons[k] + amorts[k]
        lignes.append((dates[k], t, tv * 100, flux, flux / (1 + tv) ** t))

    tab = pd.DataFrame(lignes, columns=["date_de_reglement", "maturite_residuelle_en_annee",
                                        "taux_de_valorisation", "flux", "flux_actualise"])
    tab["date_de_reglement"] = pd.to_datetime(tab["date_de_reglement"])
    tab = tab[tab["flux"] != 0].reset_index(drop=True)   # capital déjà épuisé
    prix = float(tab["flux_actualise"].sum())
    return prix, tab