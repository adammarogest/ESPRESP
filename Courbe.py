"""
Taux de référence des bons du Trésor — marché secondaire (Bank Al-Maghrib).

Python translation of the VBA macro `ScrapeBkamTable`.
Dependencies: pip install requests beautifulsoup4 pandas pyjanitor
"""



# --------------------
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import calendar
import janitor  # noqa: F401  (registers the .clean_names() method on DataFrames)
import pandas as pd
import requests
from bs4 import BeautifulSoup

BKAM_URL = (
    "https://www.bkam.ma/Marches/Principaux-indicateurs/Marche-obligataire/"
    "Marche-des-bons-de-tresor/Marche-secondaire/"
    "Taux-de-reference-des-bons-du-tresor"
)
BLOCK_ID = "e1d6b9bbf87f86f8ba53e8518e882982"

COLUMNS = [
    "Date d'échéance",
    "Transaction",
    "Taux moyen pondéré",
    "Date de la valeur",
    "Maturité résiduelle",
]


def _to_date(value: str | date | datetime) -> date:
    """Accept a date/datetime or a 'DD/MM/YYYY' string (always French order)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()


def _to_float(text: str) -> float:
    """'2,345 %' or '1 234 567,89' -> float. Returns NaN if not parsable."""
    cleaned = (
        text.replace("%", "")
        .replace("\xa0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")


def get_bkam_taux_reference(
    ref_date: str | date | datetime, timeout: int = 30
) -> pd.DataFrame:
    """
    Scrape the BKAM BDT reference-rate table for a given date.

    Parameters
    ----------
    ref_date : 'DD/MM/YYYY' string, datetime.date or datetime.datetime
    timeout  : HTTP timeout in seconds

    Returns
    -------
    DataFrame with snake_case column names (via pyjanitor clean_names):
        date_d_echeance (datetime), transaction (float),
        taux_moyen_pondere (float, in %), date_de_la_valeur (datetime),
        maturite_residuelle (int, days)
    Empty DataFrame (same columns) if BKAM has no table for that date.
    """
    d = _to_date(ref_date)

    params = {"date": d.strftime("%d/%m/%Y"), "block": BLOCK_ID}
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(BKAM_URL, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if table is None:
        return pd.DataFrame(columns=COLUMNS).clean_names()

    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 4:  # header row (<th>) or footer/total row
            continue
        rows.append(cells[:4])

    if not rows:
        return pd.DataFrame(columns=COLUMNS).clean_names()

    df = pd.DataFrame(rows, columns=COLUMNS[:4])

    for col in ("Date d'échéance", "Date de la valeur"):
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
    df["Transaction"] = df["Transaction"].map(_to_float)
    df["Taux moyen pondéré"] = df["Taux moyen pondéré"].map(_to_float)

    # Drop any row whose dates could not be parsed (e.g. a 'Total' line)
    df = df.dropna(subset=["Date d'échéance", "Date de la valeur"]).reset_index(drop=True)

    df["Maturité résiduelle"] = (
        df["Date d'échéance"] - df["Date de la valeur"]
    ).dt.days

    return df.clean_names()


# ===========================================================================
# Taux à partir de la courbe BAM — traduction Python de TauxCourbe (VBA).
# Entrée : DataFrame produit par get_bkam_taux_reference,
# colonnes 'taux_moyen_pondere' (en %) et 'maturite_residuelle' (en jours).
# ===========================================================================


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

    # --- Tableau des taux de la courbe (indices 1..34 comme en VBA) ---------
    tt = [0.0] * 35
    tt[1] = taux_courbe_brut(df, 1, d0)
    tt[2] = taux_courbe_brut(df, 91, d0)
    tt[3] = taux_courbe_brut(df, 182, d0)
    tt[4] = taux_courbe_brut(df, 364, d0)
    for i in range(5, 35):
        tt[i] = taux_courbe_brut(df, _edate_jours(d0, 12 * (i - 4)), d0)

    # --- Tableau des zéro coupons -------------------------------------------
    zz = [0.0] * 35
    for k, n in ((1, 1), (2, 91), (3, 182), (4, 364), (5, 366)):
        zz[k] = tm_ta_valo(tt[k], n, d0 + timedelta(days=n))

    # Bootstrap : obligations au pair, coupon annuel tt[j]
    for j in range(6, 35):
        temp1 = -sum(tt[j] / (1 + zz[i]) ** (i - 4) for i in range(5, j))
        zz[j] = ((1 + tt[j]) / (temp1 + 1)) ** (1 / (j - 4)) - 1

    # --- Sélection du résultat (les tests s'écrasent dans l'ordre du VBA) ---
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

    res *= 100                                               # décimal -> %
    return res if decimales is None else _arrondir(res, decimales)

# ---------------------------------------------------------------------------
# Ténors standards de la courbe
# ---------------------------------------------------------------------------

TENORS = {          # libellé -> maturité en jours
    "13s": 91,
    "26s": 182,
    "52s": 364,
    "2ans": 731,
    "5ans": 1826,
    "10ans": 3653,
    "15ans": 5479,
    "20ans": 7305,
    "30ans": 10958,
}


def tableau_tenors(df: pd.DataFrame, datevl, formate: bool = True) -> pd.DataFrame:
    """
    Taux de la courbe (taux_courbe) pour les ténors standards.

    df      : DataFrame de get_bkam_taux_reference
    datevl  : date de la courbe (date, datetime, pd.Timestamp ou 'JJ/MM/AAAA') ;
              date d'échéance de chaque ténor = datevl + maturité en jours
    formate : True  -> dates 'JJ/MM/AAAA' et taux '2.132%' (affichage)
              False -> dates en datetime et taux en float (en %), pour calculer

    Colonnes : Ténor | DATE D'ECHEANCE | Taux du JJ/MM/AAAA
    """
    d0 = _as_datetime(datevl)
    col_taux = f"Taux du {d0:%d/%m/%Y}"

    tab = pd.DataFrame({
        "Ténor": list(TENORS),
        "DATE D'ECHEANCE": [pd.Timestamp(d0 + timedelta(days=j)) for j in TENORS.values()],
        col_taux: [taux_courbe(df, j, d0) for j in TENORS.values()],
    })

    if formate:
        tab["DATE D'ECHEANCE"] = tab["DATE D'ECHEANCE"].dt.strftime("%d/%m/%Y")
        tab[col_taux] = tab[col_taux].map(lambda t: f"{t:.3f}%")
    return tab


def historique_tenors(date_debut, date_fin, jours_ouvres: bool = True,
                      pause: float = 0.5) -> pd.DataFrame:
    """
    Pour chaque date entre date_debut et date_fin (incluses) : charge la courbe
    BKAM de cette date, puis calcule les taux des ténors standards.

    date_debut, date_fin : date, datetime, pd.Timestamp ou 'JJ/MM/AAAA'
    jours_ouvres         : True -> seulement du lundi au vendredi
    pause                : secondes d'attente entre deux requêtes au site BKAM

    Colonnes : Date | 13s | 26s | 52s | 2ans | 5ans | 10ans | 15ans | 20ans | 30ans
    (taux en %, float). Les dates sans courbe publiée (jours fériés, etc.) ou en
    erreur sont ignorées et signalées par un avertissement.
    """
    import time
    import warnings

    d1, d2 = _as_datetime(date_debut), _as_datetime(date_fin)
    if d1 > d2:
        raise ValueError("date_debut doit être antérieure ou égale à date_fin")

    jours = pd.bdate_range(d1, d2) if jours_ouvres else pd.date_range(d1, d2)

    lignes, ignorees = [], []
    for i, jour in enumerate(jours):
        if i and pause:
            time.sleep(pause)
        try:
            courbe = get_bkam_taux_reference(jour.to_pydatetime())
            if len(courbe) < 2:
                ignorees.append((jour, "pas de courbe publiée"))
                continue
            taux = tableau_tenors(courbe, jour, formate=False).iloc[:, 2].tolist()
            lignes.append([jour, *taux])
        except Exception as e:                       # réseau, format inattendu…
            ignorees.append((jour, str(e)))

    if ignorees:
        detail = ", ".join(f"{j:%d/%m/%Y} ({motif})" for j, motif in ignorees)
        warnings.warn(f"{len(ignorees)} date(s) ignorée(s) : {detail}")

    return pd.DataFrame(lignes, columns=["Date", *TENORS])  