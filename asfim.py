"""
Tableaux des performances ASFIM (https://asfim.ma/publications/tableaux-des-performances/).

Le site s'appuie sur l'API publique fundshare.asfim.ma :
- liste des tableaux : /api/counter/?ordering=-date&page_size=N[&is_hebdo=true] -> date, is_hebdo ;
- fichier Excel d'une date : /api/performances/export/?date=AAAA-MM-JJ.
Les fichiers téléchargés sont gardés dans le dossier « asfim » de l'application (un tableau publié ne
change plus) ; un fichier peut aussi être chargé à la main si le réseau bloque le site.

Analyses :
- filtres : Souscripteurs = FGP, Affectation des résultats = CAPITALISANT, AN > 200 000 000
  (sauf la catégorie ACTIONS, sans filtre d'actif) ;
- classement de chaque catégorie sur la performance courte (1 jour en quotidien, 1 semaine en
  hebdomadaire) et le YTD, avec la société de gestion de chaque OPCVM et la place des fonds MAROGEST ;
- tableaux hebdomadaires : deux classements de plus, sans filtre (FONDS DÉDIÉS CAM, SECUR RENDEMENT) ;
- actif net géré par société de gestion et par catégorie ;
- deux dates hebdomadaires : performance de la VL sur la période, mêmes classements, variation de
  l'actif par société de gestion et par catégorie.
"""
from __future__ import annotations

import difflib
import io
import re
import time
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

DOSSIER = Path(__file__).resolve().parent
CACHE = DOSSIER / "asfim"
API = "https://fundshare.asfim.ma/api"
SITE = "https://asfim.ma/publications/tableaux-des-performances/"
ENTETES = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dashboard-courbe",
           "Referer": "https://asfim.ma/", "Accept": "*/*"}

SG_SUIVIE = "MAROGEST"
SEUIL_AN = 200_000_000
CATEGORIES = ["MONÉTAIRE", "OCT", "OMLT", "DIVERSIFIÉ", "ACTIONS", "CONTRACTUEL"]
FONDS_DEDIES_CAM = ["FCP MAROGEST BONDS", "CAPITAL TRUST BONDS", "FCP EMERGENCE OBLIHORIZON"]
SECUR_RENDEMENT = [
    "WINEO SECUR RENDEMENT", "AL IJTIMAI ADDAMANE", "CCM SECUR RENDEMENT", "AL IJTIMAI PATRIMOINE",
    "AL IJTIMAI AVENIR", "CT SECUR RENDEMENT", "AL IJTIMAI PROTECTION", "AD SECUR RENDEMENT",
    "AL IJTIMAI SERENITE", "AL IJTIMAI SOLIDARITE", "FCP AL IJTIMAI SECURITE", "MAROGEST SECUR RENDEMENT",
    "FCP RM SECUR RENDEMENT", "AFG SECUR RENDEMENT", "AC SECUR RENDEMENT", "IRG SECUR RENDEMENT",
    "STERLING SECUR RENDEMENT", "TWIN SECUR RENDEMENT", "FCP QUANTUM SECUR RENDEMENT",
    "FCP ALPHAVEST SECUR RENDEMENT",
]
GROUPES_HEBDO = {"FONDS DÉDIÉS CAM": FONDS_DEDIES_CAM, "SECUR RENDEMENT": SECUR_RENDEMENT}

COLONNES = {
    "CODE ISIN": "isin", "Code Maroclear": "code", "OPCVM": "opcvm", "Société de Gestion": "sg",
    "Nature juridique": "nature", "Classification": "categorie", "Sensibilité": "sensibilite",
    "Indice Bentchmark": "indice", "Indice Benchmark": "indice", "Périodicité VL": "periodicite_vl",
    "Souscripteurs": "souscripteurs", "Affectation des résultats": "affectation",
    "Commission de souscription": "comm_souscription", "Commission de rachat": "comm_rachat",
    "Frais de gestion": "frais", "Dépositaire": "depositaire", "Réseau placeur": "reseau",
    "AN": "an", "VL": "vl", "YTD": "ytd", "1 jour": "j1", "1 semaine": "s1", "1 mois": "m1",
    "3 mois": "m3", "6 mois": "m6", "1 an": "a1", "2 ans": "a2", "3 ans": "a3", "5 ans": "a5",
}
NUMERIQUES = ["comm_souscription", "comm_rachat", "frais", "an", "vl", "ytd", "j1", "s1", "m1", "m3", "m6",
              "a1", "a2", "a3", "a5"]
TEXTES = ["isin", "code", "opcvm", "sg", "nature", "categorie", "sensibilite", "indice", "periodicite_vl",
          "souscripteurs", "affectation", "depositaire", "reseau"]


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", s.upper())).strip()


# ---------------------------------------------------------------------------
# API et cache
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 25):
    import requests
    r = requests.get(url, headers=ENTETES, timeout=timeout)
    r.raise_for_status()
    return r


@lru_cache(maxsize=8)
def _liste(hebdo: bool | None, n: int, _quart_heure: int) -> pd.DataFrame:
    url = f"{API}/counter/?ordering=-date&page=1&page_size={n}" + ("&is_hebdo=true" if hebdo else "")
    res = _get(url).json().get("results", [])
    d = pd.DataFrame(res)
    if d.empty:
        return pd.DataFrame(columns=["date", "is_hebdo"])
    d = d[d.get("is_published", True) != False]  # noqa: E712
    d["date"] = pd.to_datetime(d["date"]).dt.date
    return d[["date", "is_hebdo"]].reset_index(drop=True)


def liste_tableaux(hebdo: bool | None = None, n: int = 120) -> pd.DataFrame:
    """Tableaux publiés (date, is_hebdo), du plus récent au plus ancien ; liste rafraîchie tous les quarts d'heure."""
    return _liste(hebdo, n, int(time.time() // 900))


def liste_locale() -> pd.DataFrame:
    """Tableaux déjà présents dans le dossier asfim (utile hors connexion)."""
    lignes = []
    for f in CACHE.glob("ASFIM_*.xlsx"):
        m = re.match(r"ASFIM_(\d{4}-\d{2}-\d{2})_(Q|H)\.xlsx", f.name)
        if m:
            lignes.append({"date": date.fromisoformat(m.group(1)), "is_hebdo": m.group(2) == "H"})
    return pd.DataFrame(lignes, columns=["date", "is_hebdo"]).sort_values("date", ascending=False).reset_index(drop=True)


def chemin_cache(d: date, hebdo: bool) -> Path:
    return CACHE / f"ASFIM_{d.isoformat()}_{'H' if hebdo else 'Q'}.xlsx"


def telecharger(d: date, hebdo: bool | None = None) -> Path:
    """Fichier du tableau de la date d (téléchargé une fois, puis lu dans le dossier asfim)."""
    d = pd.Timestamp(d).date()
    for h in ([hebdo] if hebdo is not None else [True, False]):
        c = chemin_cache(d, h)
        if c.exists() and c.stat().st_size > 0:
            return c
    contenu = _get(f"{API}/performances/export/?date={d.isoformat()}", timeout=60).content
    if not contenu.startswith(b"PK"):
        raise ValueError(f"Pas de tableau ASFIM publié au {d:%d/%m/%Y}.")
    if hebdo is None:
        hebdo = lire_octets(contenu)["hebdo"]
    CACHE.mkdir(exist_ok=True)
    c = chemin_cache(d, hebdo)
    c.write_bytes(contenu)
    return c


def dernier_tableau() -> tuple[date, bool]:
    l = liste_tableaux(None, 5)
    if l.empty:
        raise ValueError("La liste des tableaux ASFIM est vide.")
    return l.iloc[0]["date"], bool(l.iloc[0]["is_hebdo"])


def enregistrer_manuel(contenu: bytes) -> Path:
    """Fichier chargé à la main (même format que l'export ASFIM) : rangé dans le dossier asfim."""
    t = lire_octets(contenu)
    CACHE.mkdir(exist_ok=True)
    c = chemin_cache(t["date"], t["hebdo"])
    c.write_bytes(contenu)
    return c


# ---------------------------------------------------------------------------
# Lecture d'un tableau
# ---------------------------------------------------------------------------

def _nombre(s: pd.Series) -> pd.Series:
    if s.dtype.kind in "if":
        return s.astype(float)
    t = s.astype(str).str.replace(" ", "", regex=False).str.replace(" ", "", regex=False) \
        .str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(t.where(~t.isin(["-", "", "nan", "None", "NaN"])), errors="coerce")


def lire_octets(contenu: bytes) -> dict:
    """{"date", "hebdo", "titre", "data"} ; data : une ligne par OPCVM, colonnes normalisées (perfs en décimal)."""
    brut = pd.read_excel(io.BytesIO(contenu), header=None, dtype=object)
    titre = str(brut.iloc[0, 0] or "")
    ligne_entete = next(i for i in range(min(10, len(brut)))
                        if any(str(v).strip().upper() == "OPCVM" for v in brut.iloc[i].tolist()))
    entetes = [str(v).strip() for v in brut.iloc[ligne_entete].tolist()]
    data = brut.iloc[ligne_entete + 1:].copy()
    data.columns = [COLONNES.get(e, norm(e).lower().replace(" ", "_") or f"col{i}") for i, e in enumerate(entetes)]
    data = data[data["opcvm"].notna() & (data["opcvm"].astype(str).str.strip() != "")]
    for c in TEXTES:
        if c in data:
            data[c] = data[c].astype(str).str.strip().replace({"nan": "", "None": ""})
    for c in NUMERIQUES:
        if c in data:
            data[c] = _nombre(data[c])
    data["categorie"] = data["categorie"].str.upper()
    data["nom_norm"] = data["opcvm"].map(norm)
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", titre)
    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    hebdo = "HEBDO" in norm(titre)
    return {"date": d, "hebdo": hebdo, "titre": titre, "data": data.reset_index(drop=True)}


@lru_cache(maxsize=16)
def _lire_fichier(chemin: str, mtime: int) -> dict:
    return lire_octets(Path(chemin).read_bytes())


def lire(chemin: Path | str) -> dict:
    p = Path(chemin)
    return _lire_fichier(str(p), p.stat().st_mtime_ns)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------

def col_courte(hebdo: bool) -> tuple[str, str]:
    """Performance courte : 1 jour (tableau quotidien) ou 1 semaine (hebdomadaire, où 1 jour est vide)."""
    return ("s1", "1 semaine") if hebdo else ("j1", "1 jour")


def filtrer(df: pd.DataFrame, categorie: str, colonne_an: str = "an") -> pd.DataFrame:
    """FGP, CAPITALISANT, AN > 200 M (sauf ACTIONS) dans la catégorie."""
    m = (df["categorie"] == categorie) & (df["souscripteurs"].str.upper() == "FGP") \
        & (df["affectation"].str.upper() == "CAPITALISANT")
    if categorie != "ACTIONS":
        m &= df[colonne_an] > SEUIL_AN
    return df[m]


def fonds_du_groupe(df: pd.DataFrame, noms: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Fonds d'une liste de noms (nom exact normalisé, sinon le plus proche à 85 %) ; renvoie aussi les noms introuvables."""
    index = dict(zip(df["nom_norm"], df.index))
    trouves, absents = [], []
    for n in dict.fromkeys(noms):
        k = norm(n)
        if k not in index:
            proche = difflib.get_close_matches(k, list(index), n=1, cutoff=0.85)
            k = proche[0] if proche else None
        (trouves.append(index[k]) if k else absents.append(n))
    return df.loc[list(dict.fromkeys(trouves))], absents


def classer(df: pd.DataFrame, colonnes: list[str]) -> pd.DataFrame:
    """Rangs (1 = meilleure performance) sur chaque colonne ; fonds sans performance non classés."""
    out = df.copy()
    for c in colonnes:
        out[f"rang_{c}"] = out[c].rank(ascending=False, method="min")
    out["nb"] = len(out)
    return out.sort_values(f"rang_{colonnes[-1]}", na_position="last")


def classements(t: dict) -> dict[str, pd.DataFrame]:
    """{catégorie : classement} pour un tableau ; hebdomadaire : + FONDS DÉDIÉS CAM et SECUR RENDEMENT (sans filtre)."""
    df, (c, _) = t["data"], col_courte(t["hebdo"])
    res = {cat: classer(filtrer(df, cat), [c, "ytd"]) for cat in CATEGORIES if (df["categorie"] == cat).any()}
    if t["hebdo"]:
        for nom, liste in GROUPES_HEBDO.items():
            g, absents = fonds_du_groupe(df, liste)
            r = classer(g, [c, "ytd"])
            r.attrs["absents"] = absents
            res[nom] = r
    return res


def synthese_sg(cl: dict[str, pd.DataFrame], colonnes: list[str], sg: str = SG_SUIVIE) -> pd.DataFrame:
    """Place des fonds de la société suivie dans chaque classement."""
    lignes = []
    for cat, r in cl.items():
        nb = {c: int(r[c].notna().sum()) for c in colonnes}
        for _, f in r[r["sg"].str.upper() == sg.upper()].iterrows():
            ligne = {"categorie": cat, "opcvm": f["opcvm"], "an": f.get("an", np.nan), "nb_fonds": len(r)}
            for c in colonnes:
                ligne[c] = f[c]
                ligne[f"rang_{c}"] = f[f"rang_{c}"]
                ligne[f"rang_{c}_txt"] = (f"{int(f[f'rang_{c}'])} / {nb[c]}" if pd.notna(f[f"rang_{c}"]) else "—")
            lignes.append(ligne)
    return pd.DataFrame(lignes)


def actif_net(df: pd.DataFrame, colonne: str = "an") -> dict:
    """AN par société de gestion × catégorie, rangs par catégorie, total, part de marché."""
    p = df.pivot_table(index="sg", columns="categorie", values=colonne, aggfunc="sum", fill_value=0.0)
    cats = [c for c in CATEGORIES if c in p.columns] + [c for c in p.columns if c not in CATEGORIES]
    p = p[cats]
    p["TOTAL"] = p.sum(axis=1)
    p = p.sort_values("TOTAL", ascending=False)
    rangs = p.rank(ascending=False, method="min").where(p > 0)
    parts = p / p.sum()
    return {"an": p, "rangs": rangs, "parts": parts, "categories": cats}


def actif_par_categorie(df: pd.DataFrame, colonne: str = "an") -> pd.DataFrame:
    g = df.groupby("categorie").agg(nb=("opcvm", "size"), an=(colonne, "sum"))
    return g.reindex([c for c in CATEGORIES if c in g.index] + [c for c in g.index if c not in CATEGORIES])


# ---------------------------------------------------------------------------
# Deux dates hebdomadaires
# ---------------------------------------------------------------------------

def apparier(t1: dict, t2: dict) -> pd.DataFrame:
    """Fonds présents aux deux dates (ISIN, sinon nom) ; perf = VL2 / VL1 − 1 ; caractéristiques de la date 2."""
    a, b = t1["data"], t2["data"]
    cle = lambda d: np.where(d["isin"].str.len() >= 10, d["isin"], "N:" + d["nom_norm"])
    a, b = a.assign(cle=cle(a)), b.assign(cle=cle(b))
    m = b.merge(a[["cle", "vl", "an"]].rename(columns={"vl": "vl1", "an": "an1"}), on="cle", how="left")
    m = m.rename(columns={"vl": "vl2", "an": "an2"})
    m["perf"] = m["vl2"] / m["vl1"] - 1
    m["an"] = m["an2"]
    m["var_an"] = m["an2"] - m["an1"]
    return m


def classements_periode(m: pd.DataFrame) -> dict[str, pd.DataFrame]:
    res = {cat: classer(filtrer(m, cat), ["ytd", "perf"]) for cat in CATEGORIES if (m["categorie"] == cat).any()}
    for nom, liste in GROUPES_HEBDO.items():
        g, absents = fonds_du_groupe(m, liste)
        r = classer(g, ["ytd", "perf"])
        r.attrs["absents"] = absents
        res[nom] = r
    return res


def variation_actif(t1: dict, t2: dict) -> dict:
    """Variation de l'actif net par société de gestion, par catégorie et société × catégorie (tous les fonds)."""
    def agg(df, cles):
        return df.groupby(cles)["an"].sum()
    a, b = t1["data"], t2["data"]
    sg = pd.concat([agg(a, "sg").rename("an1"), agg(b, "sg").rename("an2")], axis=1).fillna(0.0)
    cat = pd.concat([agg(a, "categorie").rename("an1"), agg(b, "categorie").rename("an2")], axis=1).fillna(0.0)
    for x in (sg, cat):
        x["variation"] = x["an2"] - x["an1"]
        x["variation_pct"] = np.where(x["an1"] > 0, x["an2"] / x["an1"] - 1, np.nan)
        x["part1"] = x["an1"] / x["an1"].sum()
        x["part2"] = x["an2"] / x["an2"].sum()
        x["var_part_pb"] = (x["part2"] - x["part1"]) * 1e4
    sg = sg.sort_values("an2", ascending=False)
    sg["rang1"] = sg["an1"].rank(ascending=False, method="min")
    sg["rang2"] = sg["an2"].rank(ascending=False, method="min")
    cat = cat.reindex([c for c in CATEGORIES if c in cat.index] + [c for c in cat.index if c not in CATEGORIES])
    croise = (agg(b, ["sg", "categorie"]).unstack(fill_value=0.0)
              .sub(agg(a, ["sg", "categorie"]).unstack(fill_value=0.0), fill_value=0.0))
    croise = croise.reindex(sg.index).fillna(0.0)
    croise = croise[[c for c in CATEGORIES if c in croise.columns] + [c for c in croise.columns if c not in CATEGORIES]]
    croise["TOTAL"] = croise.sum(axis=1)
    return {"sg": sg, "categorie": cat, "croise": croise}
