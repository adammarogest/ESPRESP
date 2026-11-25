"""
Dette privée : répertoire des émissions (Dette_Prive.xlsx ou Dette_Prive.xlsm, feuille « Debts »,
dans le dossier de l'application). Le fichier est relu automatiquement dès qu'il est modifié.

- Recherche par code : 202943 (ou l'ISIN MA0002029439) ; code = INSTRID sans « MA », sans les zéros
  de tête et sans le dernier chiffre (clé de contrôle) : MA000 202943 9.
- Recherche par émetteur (PREFERREDNAMEISSUER) avec suggestions des noms les plus proches.
- Fiche, maturité initiale, ténors de la courbe (pleins ou encadrants), historique de la semaine
  précédant l'émission, statistiques et échéancier des lignes actives d'un émetteur.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

DOSSIER = Path(__file__).resolve().parent
NOMS = ("Dette_Prive.xlsx", "Dette_Prive.xlsm", "DETTE_PRIVE.xlsx", "DETTE_PRIVE.xlsm")

CATEGORIES = {"OBL_ORDN": "Obligation ordinaire", "OBL_CONV": "Obligation convertible",
              "OBL_SUBD": "Obligation subordonnée", "FPCT": "FPCT", "BDT": "BDT", "TCN": "TCN"}
TYPES_TAUX = {"FIXD": "Fixe", "FLTG": "Révisable", "FLOT": "Révisable", "DISC": "Précompté (zéro coupon)"}
REVISABLES = {"FLTG", "FLOT"}
PERIODES = {"ANLY": "Annuelle", "HFLY": "Semestrielle", "QTLY": "Trimestrielle", "MNLY": "Mensuelle",
            "MNTH": "Mensuelle", "BMLY": "Bimestrielle", "SMAL": "Semestrielle", "ONRD": "À l'échéance"}
STATUTS = {"ACTI": "Active", "DLET": "Échue / radiée", "TODL": "En cours de radiation", "INVD": "Invalide",
           "ICPT": "Incomplète"}
# Ténors de la courbe BKAM (jours), comme base_courbe.TENORS
TENORS = {"13s": 91, "26s": 182, "52s": 364, "2ans": 731, "5ans": 1826, "10ans": 3653, "15ans": 5479,
          "20ans": 7305, "30ans": 10958}
LIB_TENORS = {"13s": "13 semaines", "26s": "26 semaines", "52s": "52 semaines", "2ans": "2 ans", "5ans": "5 ans",
              "10ans": "10 ans", "15ans": "15 ans", "20ans": "20 ans", "30ans": "30 ans"}
TOLERANCE_JOURS = 10          # maturité « pleine » : à ± 10 jours d'un ténor de la courbe


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def chemin() -> Path | None:
    for n in NOMS:
        if (DOSSIER / n).exists():
            return DOSSIER / n
    autres = sorted(DOSSIER.glob("[Dd]ette*[Pp]riv*.xls*"))
    return autres[0] if autres else None


def code_depuis_isin(isin) -> str:
    """MA0002029439 -> 202943 (sans MA, sans zéros de tête, sans la clé de contrôle)."""
    s = str(isin or "").strip().upper()
    if s.startswith("MA") and len(s) > 3:
        s = s[2:-1]
    return s.lstrip("0") or s


def _date(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() in ("", "nan", "NaT"):
        return pd.NaT
    return pd.to_datetime(str(v)[:10], errors="coerce")


def _dates_coupon(v) -> list[date]:
    """Dates de coupon triées, sans doublons ; deux dates à moins de 10 jours : la plus tardive seulement."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    dates = set()
    for m in re.split(r"[;,\s]+", str(v).strip("{} ")):
        try:
            dates.add(date.fromisoformat(m[:10]))
        except ValueError:
            continue
    propres = []
    for d in sorted(dates):
        if propres and (d - propres[-1]).days < 10:
            propres[-1] = d
        else:
            propres.append(d)
    return propres


def frequence(dates: list[date], periode: str | None = None) -> tuple[str, int | None]:
    """Fréquence déduite des dates de coupon triées (écart médian en mois) ; à défaut INTERESTPERIODCTY."""
    if len(dates) >= 2:
        ecarts = [(b.year - a.year) * 12 + b.month - a.month + (b.day - a.day) / 30 for a, b in zip(dates, dates[1:])]
        m = float(np.median(ecarts))
        for lib, mois in (("Mensuelle", 1), ("Bimestrielle", 2), ("Trimestrielle", 3), ("Semestrielle", 6),
                          ("Annuelle", 12)):
            if abs(m - mois) <= max(0.35, mois * 0.12):
                return lib, mois
        return f"Irrégulière (~{m:.1f} mois)", max(1, int(round(m)))
    lib = PERIODES.get(str(periode or "").upper())
    mois = {"Annuelle": 12, "Semestrielle": 6, "Trimestrielle": 3, "Mensuelle": 1, "Bimestrielle": 2}.get(lib)
    if len(dates) == 1:
        return (f"{lib} (une seule date de coupon)" if lib else "Une seule date de coupon"), mois
    return lib or "—", mois


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9 ]+", " ", s.upper()).strip()


@lru_cache(maxsize=2)
def _charger(chemin_: str, mtime: int) -> pd.DataFrame:
    xl = pd.ExcelFile(chemin_)
    feuille = "Debts" if "Debts" in xl.sheet_names else xl.sheet_names[0]
    brut = xl.parse(feuille, dtype={"INSTRID": str})
    brut.columns = [str(c).strip() for c in brut.columns]
    d = pd.DataFrame()
    d["isin"] = brut["INSTRID"].astype(str).str.strip().str.upper()
    d["code"] = d["isin"].map(code_depuis_isin)
    d["instrtype"] = brut.get("INSTRTYPE", "").astype(str).str.strip().str.upper()
    d["ctg"] = brut.get("INSTRCTGRY", "").astype(str).str.strip().str.upper()
    d["categorie"] = d["ctg"].map(CATEGORIES).fillna(d["ctg"])
    d["description"] = brut.get("ENGLONGNAME", "").fillna(brut.get("ENGPREFERREDNAME", "")).astype(str).str.strip()
    d["paiement"] = np.where(d["instrtype"].isin(["ZCBD", "AMBD"]), "Amortissable", "In fine")
    d["it"] = brut.get("INTERESTTYPE", "").astype(str).str.strip().str.upper()
    d["type_taux"] = d["it"].map(TYPES_TAUX).fillna(d["it"].replace("NAN", "—"))
    d["revisable"] = d["it"].isin(REVISABLES)
    d["forme"] = np.where(d["revisable"], d["paiement"] + " révisable", d["paiement"] + " taux fixe")
    d.loc[d["it"] == "DISC", "forme"] = "Zéro coupon"
    d["nb_titres"] = pd.to_numeric(brut.get("ISSUESIZE"), errors="coerce")
    d["nominal"] = pd.to_numeric(brut.get("PARVALUE"), errors="coerce")
    d["montant"] = d["nb_titres"] * d["nominal"]
    d["date_em"] = pd.to_datetime(brut.get("ISSUEDT").astype(str).str[:10], errors="coerce", format="%Y-%m-%d")
    d["date_ech"] = pd.to_datetime(brut.get("MATURITYDT_L").astype(str).str[:10], errors="coerce", format="%Y-%m-%d")
    d["taux"] = pd.to_numeric(brut.get("INTERESTRATE"), errors="coerce")
    d["periode"] = brut.get("INTERESTPERIODCTY", "").astype(str).str.strip().str.upper()
    d["amortfreq"] = brut.get("AMORTFREQ", "").astype(str).str.strip().str.upper().replace("NAN", "")
    d["garantie"] = brut.get("GUARANTEE", "").astype(str).replace("nan", "")
    d["emetteur"] = brut.get("PREFERREDNAMEISSUER", "").astype(str).str.strip().replace("nan", "—")
    d["registrar"] = brut.get("PREFERREDNAMEREGISTRAR", "").astype(str).str.strip().replace("nan", "—")
    d["statut_code"] = brut.get("INSTRSTATUS", "").astype(str).str.strip().str.upper()
    d["statut"] = d["statut_code"].map(STATUTS).fillna(d["statut_code"])
    d["coupons"] = brut.get("CouponPayDates").map(_dates_coupon) if "CouponPayDates" in brut else [[]] * len(brut)
    freq = [("À l'échéance (coupon unique)", None) if len(c) == 1 and pd.notna(e) and abs((c[0] - e.date()).days) < 10
            else frequence(c, p) for c, p, e in zip(d["coupons"], d["periode"], d["date_ech"])]
    d["frequence"] = [f[0] for f in freq]
    d["freq_mois"] = [f[1] for f in freq]
    d["maturite_jours"] = (d["date_ech"] - d["date_em"]).dt.days
    d["maturite"] = d["maturite_jours"].map(libelle_maturite)
    d["annee"] = d["date_em"].dt.year
    d["freq_amort"] = np.where(d["paiement"] == "Amortissable",
                               d["amortfreq"].map(PERIODES).fillna(pd.Series(d["frequence"]).str.split(" ").str[0]),
                               "In fine (à l'échéance)")
    d.loc[d["it"] == "DISC", "freq_amort"] = "À l'échéance"
    d["emetteur_norm"] = d["emetteur"].map(_norm)
    d["desc_norm"] = d["description"].map(_norm)
    return d


def base() -> pd.DataFrame:
    c = chemin()
    if c is None:
        raise FileNotFoundError("Dette_Prive.xlsx introuvable dans le dossier de l'application.")
    return _charger(str(c), c.stat().st_mtime_ns)


def info() -> str:
    c = chemin()
    if c is None:
        return "Dette_Prive.xlsx absent du dossier de l'application."
    d = base()
    maj = pd.Timestamp(c.stat().st_mtime, unit="s").strftime("%d/%m/%Y %H:%M")
    lignes = f"{len(d):,}".replace(",", " ")
    return f"{c.name} : {lignes} lignes, {d['emetteur'].nunique()} émetteurs (fichier du {maj})."


# ---------------------------------------------------------------------------
# Maturité et ténors
# ---------------------------------------------------------------------------

def libelle_maturite(jours) -> str:
    """Maturité initiale lisible : semaines sous un an, années ensuite (arrondies au mois)."""
    if jours is None or pd.isna(jours) or jours <= 0:
        return "—"
    jours = int(jours)
    if jours < 350:
        return f"{int(round(jours / 7))} semaines"
    if jours <= 380:
        return "52 semaines" if jours <= 366 else "1 an"
    mois = int(round(jours / 30.4375))
    a, m = divmod(mois, 12)
    if m in (0,) or a >= 3 and m in (1, 11):
        a = int(round(mois / 12))
        return f"{a} an{'s' if a > 1 else ''}"
    return f"{a} an{'s' if a > 1 else ''} {m} mois"


def tenors_de(jours: int) -> tuple[list[str], bool]:
    """(ténors, pleine) : le ténor de la courbe si la maturité en est un (± 10 j), sinon les deux qui l'encadrent."""
    noms = list(TENORS)
    for t in noms:
        if abs(jours - TENORS[t]) <= TOLERANCE_JOURS:
            return [t], True
    if jours < TENORS[noms[0]]:
        return [noms[0]], False
    if jours > TENORS[noms[-1]]:
        return [noms[-1]], False
    for a, b in zip(noms, noms[1:]):
        if TENORS[a] < jours < TENORS[b]:
            return [a, b], False
    return [noms[-1]], False


def historique_tenors(conn, date_em, tenors: list[str]) -> pd.DataFrame:
    """Taux des ténors de la courbe sur la semaine précédant l'émission (J-6 à J)."""
    import base_courbe as db
    fin = pd.Timestamp(date_em).date()
    h = db.lire_historique(conn, fin - timedelta(days=6), fin)
    return h[["Date", *tenors]] if len(h) else pd.DataFrame(columns=["Date", *tenors])


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def chercher_code(saisie: str) -> pd.Series | None:
    """Ligne du code (202943) ou de l'ISIN (MA0002029439) ; la plus récente s'il y a des doublons."""
    s = str(saisie or "").strip().upper()
    if not s:
        return None
    d = base()
    m = d[d["isin"] == s] if s.startswith("MA") and len(s) == 12 else d[d["code"] == code_depuis_isin(s)]
    if m.empty:
        return None
    return m.sort_values("date_em").iloc[-1]


def emetteurs() -> pd.DataFrame:
    d = base()
    g = d.groupby("emetteur").agg(n=("isin", "size"), derniere=("date_em", "max")).reset_index()
    return g.sort_values("n", ascending=False)


def suggestions(saisie: str, n: int = 40) -> list[tuple[str, float]]:
    """Émetteurs les plus proches de la saisie (score 0-1) : contient la saisie, puis similarité des noms et des mots."""
    e = emetteurs()
    if not str(saisie or "").strip():
        return [(r.emetteur, 0.0) for r in e.itertuples()][:n]
    q = _norm(saisie)
    mots_q = q.split()
    d = base()
    desc_par_emetteur = {k: g for k, g in d.groupby("emetteur")["desc_norm"]}
    res = []
    for r in e.itertuples():
        nom = _norm(r.emetteur)
        score = difflib.SequenceMatcher(None, q, nom).ratio()
        if q and q in nom:
            score = max(score, 0.9 + 0.1 * len(q) / max(len(nom), 1))
        elif nom and nom in q:
            score = max(score, 0.85)
        if len(q) >= 3:                                   # nom courant dans les descriptions (ATTIJARI -> ATW E)
            part = desc_par_emetteur.get(r.emetteur, pd.Series(dtype=str)).str.contains(q, regex=False).mean()
            if part and part > 0:
                score = max(score, 0.8 + 0.1 * float(part))
        mots = nom.split()
        if mots_q and mots:
            par_mot = np.mean([max(difflib.SequenceMatcher(None, a, b).ratio() for b in mots) for a in mots_q])
            score = max(score, 0.95 * par_mot)
        res.append((r.emetteur, score + min(r.n, 500) / 1e6))       # à égalité : l'émetteur le plus actif
    res.sort(key=lambda x: -x[1])
    return res[:n]


# ---------------------------------------------------------------------------
# Fiche titre
# ---------------------------------------------------------------------------

def fiche(saisie: str) -> dict | None:
    r = chercher_code(saisie)
    if r is None:
        return None
    prime, dans_base, desc_base = None, False, None
    try:
        import base_titre as BT
        f = BT.fiche(r["code"])
        if f:
            dans_base, prime, desc_base = True, f.get("prime"), f.get("description")
    except Exception:
        pass
    jours = int(r["maturite_jours"]) if pd.notna(r["maturite_jours"]) else None
    tenors, pleine = tenors_de(jours) if jours else ([], False)
    return {**r.to_dict(), "dans_base": dans_base, "prime": prime, "description_base": desc_base,
            "tenors": tenors, "pleine": pleine,
            "tenors_hist": tenors + (["52s"] if r["revisable"] and "52s" not in tenors else [])}


# ---------------------------------------------------------------------------
# Émetteur
# ---------------------------------------------------------------------------

def emissions(emetteur: str) -> pd.DataFrame:
    d = base()
    return d[d["emetteur"] == emetteur].sort_values("date_em")


def par_annee(e: pd.DataFrame) -> pd.DataFrame:
    g = e.dropna(subset=["annee"]).groupby("annee").agg(nombre=("isin", "size"), montant=("montant", "sum"))
    if g.empty:
        return g.reset_index()
    toutes = range(int(g.index.min()), int(g.index.max()) + 1)
    return g.reindex(toutes, fill_value=0).rename_axis("annee").reset_index()


def actives(e: pd.DataFrame, jour: date | None = None) -> pd.DataFrame:
    jour = pd.Timestamp(jour or date.today())
    return e[(e["date_ech"] > jour) & (~e["statut_code"].isin(["DLET", "INVD"]))]


def _dates_theoriques(em: date, ech: date, mois: int) -> list[date]:
    """Dates de tombée reconstituées à rebours depuis l'échéance, tous les `mois` mois, après la date d'émission."""
    from dateutil.relativedelta import relativedelta
    dates, k = [], 0
    while True:
        d = ech - relativedelta(months=mois * k)
        if d <= em:
            break
        dates.append(d)
        k += 1
    return sorted(dates)


def echeancier_ligne(r, jour: date | None = None) -> list[dict]:
    """
    Tombées futures approchées d'une ligne active (montants pour toute l'émission) :
    - dates : CouponPayDates s'il couvre l'échéance, sinon reconstituées à rebours depuis l'échéance
      selon la fréquence (annuelle par défaut) ;
    - amortissable : amortissement linéaire sur toutes les dates depuis l'émission ;
    - coupon = capital restant dû × taux × mois de la période / 12 (zéro coupon : pas de coupon,
      remboursement du nominal à l'échéance) ; révisable : dernier taux connu.
    """
    jour = jour or date.today()
    em, ech = pd.Timestamp(r["date_em"]).date(), pd.Timestamp(r["date_ech"]).date()
    mois = r["freq_mois"]
    mois = 12 if mois is None or pd.isna(mois) or mois <= 0 else int(mois)
    dates = [d for d in r["coupons"] if em < d <= ech]
    if not dates or dates[-1] < ech - timedelta(days=7):
        dates = _dates_theoriques(em, ech, mois)
    if not dates:
        dates = [ech]
    montant = float(r["montant"] or 0)
    taux = float(r["taux"]) / 100 if pd.notna(r["taux"]) else 0.0
    zc = r["it"] == "DISC"
    amort = r["paiement"] == "Amortissable" and not zc
    n = len(dates)
    lignes, crd, prec = [], montant, em
    for i, d in enumerate(dates):
        capital = montant / n if amort else (montant if i == n - 1 else 0.0)
        frac = ((d.year - prec.year) * 12 + d.month - prec.month + (d.day - prec.day) / 30.4375) / 12
        coupon = 0.0 if zc else crd * taux * max(frac, 0)
        if d > jour and coupon + capital > 0:
            lignes.append({"date": d, "code": r["code"], "isin": r["isin"], "description": r["description"],
                           "forme": r["forme"], "coupon": coupon, "capital": capital, "total": coupon + capital,
                           "crd_avant": crd})
        crd -= capital
        prec = d
    return lignes


def echeancier_emetteur(e: pd.DataFrame, jour: date | None = None) -> pd.DataFrame:
    lignes = [l for _, r in actives(e, jour).iterrows() for l in echeancier_ligne(r, jour)]
    cols = ["date", "code", "isin", "description", "forme", "coupon", "capital", "total", "crd_avant"]
    return pd.DataFrame(lignes, columns=cols).sort_values(["date", "code"]).reset_index(drop=True)


def encours(e: pd.DataFrame, jour: date | None = None) -> float:
    """Capital restant dû des lignes actives (amortissement linéaire approché)."""
    total = 0.0
    for _, r in actives(e, jour).iterrows():
        tomb = echeancier_ligne(r, jour)
        total += sum(t["capital"] for t in tomb)
    return total


def statistiques(e: pd.DataFrame) -> dict:
    dates = sorted(e["date_em"].dropna().dt.date.unique())
    ecarts = [(b - a).days for a, b in zip(dates, dates[1:])]
    annees = e["annee"].dropna()
    nb_annees = int(annees.max() - annees.min() + 1) if len(annees) else 0
    recent = e[e["date_em"] >= pd.Timestamp(date.today()) - pd.DateOffset(years=3)]
    return {
        "nombre": len(e), "montant": float(e["montant"].sum()),
        "premiere": dates[0] if dates else None, "derniere": dates[-1] if dates else None,
        "jours_emission": len(dates), "ecart_moyen": float(np.mean(ecarts)) if ecarts else None,
        "ecart_median": float(np.median(ecarts)) if ecarts else None,
        "par_an": len(e) / nb_annees if nb_annees else None, "annees_actives": int(annees.nunique()),
        "par_an_3ans": len(recent) / 3,
    }


def repartition(e: pd.DataFrame, colonne: str) -> pd.DataFrame:
    g = e.groupby(colonne).agg(nombre=("isin", "size"), montant=("montant", "sum")).reset_index()
    g["part_nombre"] = g["nombre"] / g["nombre"].sum()
    g["part_montant"] = g["montant"] / g["montant"].sum() if g["montant"].sum() else 0
    return g.sort_values(["nombre", "montant"], ascending=False).reset_index(drop=True)


def ordre_maturite(lib: str) -> float:
    m = re.match(r"(\d+) semaines", lib or "")
    if m:
        return int(m.group(1)) / 52
    m = re.match(r"(\d+) ans?(?: (\d+) mois)?", lib or "")
    if m:
        return int(m.group(1)) + (int(m.group(2) or 0)) / 12
    return 99.0
