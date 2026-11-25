"""
Contrôle de valorisation : rapprochement d'un export de valorisation (VL) avec le pricer.

Export attendu (format constant) : une ligne d'en-tête contenant au moins TITRE (code) et VALORISATION,
et en général DESCRIPTION TITRE, DATE RÉFÉRENCE, SENSIBILITÉ, DURATION, CLASSE TITRE, CATÉGORIE TITRE.
Les colonnes sont repérées par leur nom (accents et casse ignorés).

Chaque titre est cherché dans base_titre.xlsx, valorisé par pricer.valoriser (mêmes règles que le
Pricer) puis comparé : écart de prix unitaire (MAD et pb du prix), de sensibilité et de duration.

Conventions constatées sur l'export de référence (16/11/2026, 204 lignes valorisées) :
- valorisation au jour ouvré suivant la date de référence (J+1) sur la courbe de la date de référence ;
- coupon en exact/360 si METHODE_COUPON est en x/360 ou si le coupon est infra-annuel ;
- révisable à moins d'un an de sa révision : flux jusqu'à la révision actualisés au taux monétaire ;
- duration monétaire = sensibilité × (1 + taux).
"""

from __future__ import annotations

import hashlib
import io
import unicodedata
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

import base_courbe as db
import base_titre as BT
import pricer as PR
import swap as SW

STATUTS = {
    "ok": "Conforme",
    "prix": "Écart de prix",
    "sensi": "Écart de sensibilité",
    "non_valo": "Non valorisé dans le fichier",
    "absent": "Absent de la base",
    "sans_carac": "Sans caractéristiques de pricing",
    "hors": "Échu ou pas encore émis",
    "erreur": "Erreur de calcul",
}
MAL_PRICES = ("prix", "sensi")
NON_COMPAREES = ("non_valo", "absent", "sans_carac", "hors", "erreur")


# ---------------------------------------------------------------------------
# Lecture de l'export
# ---------------------------------------------------------------------------

def _norm(texte) -> str:
    t = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode()
    return " ".join(t.upper().replace("_", " ").split())


def _trouver(colonnes: list[str], *motifs, exact: str | None = None) -> str | None:
    n = {c: _norm(c) for c in colonnes}
    if exact:
        for c, v in n.items():
            if v == exact:
                return c
    for motif in motifs:
        for c, v in n.items():
            if motif in v:
                return c
    return None


def lire_export(contenu: bytes, nom: str = "") -> pd.DataFrame:
    """Export de valorisation -> DataFrame normalisé (code, description, date_ref, valo, sensi, duration…)."""
    moteur = "xlrd" if nom.lower().endswith(".xls") else None
    brut = pd.read_excel(io.BytesIO(contenu), sheet_name=0, header=None, engine=moteur, dtype=object)
    entete = next((i for i in range(min(15, len(brut)))
                   if {"TITRE", "VALORISATION"} <= {_norm(v) for v in brut.iloc[i].tolist()}
                   or any("VALORISATION" in _norm(v) for v in brut.iloc[i].tolist())), None)
    if entete is None:
        raise ValueError("ligne d'en-tête introuvable (colonnes TITRE et VALORISATION attendues)")
    df = brut.iloc[entete + 1:].copy()
    df.columns = [str(c).strip() for c in brut.iloc[entete]]
    cols = list(df.columns)
    c_code = _trouver(cols, "CODE", exact="TITRE") or _trouver(cols, "TITRE")
    c_desc = _trouver(cols, "DESCRIPTION", "LIBELLE")
    if c_code == c_desc:
        c_code = _trouver(cols, exact="TITRE")
    c_valo = _trouver(cols, "VALORISATION", "COURS", "PRIX")
    if not (c_code and c_valo):
        raise ValueError("colonnes TITRE et VALORISATION introuvables")
    c_date = _trouver(cols, "DATE")
    c_sens = _trouver(cols, "SENSIBILITE", "SENSI")
    c_dur = _trouver(cols, "DURATION")
    c_cls = _trouver(cols, "CLASSE")
    c_cat = _trouver(cols, "CATEGORIE")
    out = pd.DataFrame({
        "code": df[c_code].map(BT._code),
        "description": df[c_desc].astype(str).str.strip() if c_desc else "",
        "date_ref": df[c_date].map(BT._date) if c_date else None,
        "valo_fichier": pd.to_numeric(df[c_valo], errors="coerce"),
        "sensi_fichier": pd.to_numeric(df[c_sens], errors="coerce") if c_sens else np.nan,
        "duration_fichier": pd.to_numeric(df[c_dur], errors="coerce") if c_dur else np.nan,
        "classe": df[c_cls].astype(str).str.strip() if c_cls else "",
        "categorie": df[c_cat].astype(str).str.strip() if c_cat else "",
    })
    out = out[out["code"] != ""].reset_index(drop=True)
    out["ligne"] = out.index + entete + 2                       # numéro de ligne Excel
    return out


def date_reference(export: pd.DataFrame) -> date | None:
    d = export["date_ref"].dropna()
    return d.mode().iloc[0] if len(d) else None


def jour_ouvre_suivant(d: date) -> date:
    """Jour ouvré suivant (week-ends sautés ; jours fériés non gérés)."""
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------

def _cause(r: dict) -> str:
    if r.get("valo_zero"):
        return "Règle de gestion : valorisation forcée à 0"
    if r["statut"] == "ok":
        return ""
    if r["statut"] in NON_COMPAREES:
        return STATUTS[r["statut"]]
    if r["categorie"] == "DAT" or (r["valo_fichier"] or 0) <= 10:
        return "Ligne technique (valorisation symbolique)"
    ecart, vf, vc = r["ecart_mad"], r["valo_fichier"], r["valo_calcul"]
    if r["statut"] == "sensi" and abs(ecart) <= r["tol_prix"]:
        return "Prix conforme, sensibilité différente"
    amorti = r["type"] == "amortissable" or r.get("rev_amorti")
    if vc and abs(vf / vc - 1) > 0.01 and (amorti or r["categorie"] == "FPCT"):
        return f"Capital restant dû différent (fichier / calcul = {vf / vc:.4f}) : amortissement réel ≠ linéaire"
    if vc and abs(vf / vc - 1) > 0.05:
        return f"Nominal ou capital différent (fichier / calcul = {vf / vc:.4f})"
    coupon_5j = (r.get("crd") or r.get("nominal") or 0) * (r.get("tf") or 0) / 100 * 5 / 365
    if coupon_5j and 0.7 <= abs(ecart) / coupon_5j <= 1.3:
        return "≈ 5 jours de coupon : base du coupon à vérifier"
    if abs(r["ecart_pb"]) <= 2:
        return "Petit écart (≤ 2 pb) : arrondi ou convention de date"
    return "À analyser : taux facial, spread, dates ou révision"


def _comparer(export: pd.DataFrame, valo: date, courbe: pd.DataFrame, tol_prix: float, tol_sensi: float) -> pd.DataFrame:
    lignes = []
    for _, e in export.iterrows():
        r = {"ligne": e["ligne"], "code": e["code"], "description": e["description"], "classe": e["classe"],
             "categorie": e["categorie"], "valo_fichier": e["valo_fichier"], "sensi_fichier": e["sensi_fichier"],
             "duration_fichier": e["duration_fichier"], "tol_prix": tol_prix}
        f = BT.fiche(e["code"])
        if f is not None:
            r.update({"valo_zero": f.get("valo_zero", False), "type": f["type"], "type_lib": PR.TYPES.get(f["type"], f["type"]) + (" amort." if f["rev_amorti"] else ""),
                      "rev_amorti": f["rev_amorti"], "emetteur": f["emetteur"], "echeance": f["ech"], "tf": f["tf"],
                      "prime": f["prime"], "nominal": f["nominal"], "base_coupon": "Exact/360" if f["base_coupon"] == "360"
                      else "Exact/exact", "grace": f.get("grace"), "description": e["description"] or f["description"]})
        if f is not None and f.get("valo_zero"):
            vf = e["valo_fichier"] if pd.notna(e["valo_fichier"]) else 0.0
            r.update({"valo_calcul": 0.0, "sensi_calcul": 0.0, "duration_calcul": 0.0, "ecart_mad": -vf,
                      "ecart_pb": np.nan, "ecart_sensi": 0.0 - (e["sensi_fichier"] if pd.notna(e["sensi_fichier"]) else 0.0),
                      "statut": "ok" if abs(vf) <= tol_prix else "prix"})
        elif not (pd.notna(e["valo_fichier"]) and e["valo_fichier"] > 0):
            r["statut"] = "non_valo"
        elif f is None:
            r["statut"] = "absent"
        elif not f["valorisable"]:
            r["statut"] = "sans_carac"
        elif not (f["ech"] and f["ech"] > valo and (f["em"] or f["jouis"]) and (f["em"] or f["jouis"]) <= valo):
            r["statut"] = "hors"
        else:
            try:
                v = PR.valoriser(SW._parametres(f, valo), courbe)
            except Exception as ex:                                  # noqa: BLE001
                r.update(statut="erreur", erreur=str(ex))
            else:
                r.update({"valo_calcul": v["valeur"], "sensi_calcul": v["sensibilite"], "duration_calcul": v["duration"],
                          "taux": v["taux"], "ech_eff": v["ech_eff"], "crd": v["crd"], "cc": v["cc"],
                          "monetaire": v["monetaire"], "tf_coupon": v["tf_coupon"]})
                r["ecart_mad"] = v["valeur"] - e["valo_fichier"]
                r["ecart_pb"] = r["ecart_mad"] / e["valo_fichier"] * 1e4
                r["ecart_sensi"] = v["sensibilite"] - e["sensi_fichier"] if pd.notna(e["sensi_fichier"]) else np.nan
                r["ecart_duration"] = v["duration"] - e["duration_fichier"] if pd.notna(e["duration_fichier"]) else np.nan
                if abs(r["ecart_mad"]) > tol_prix:
                    r["statut"] = "prix"
                elif pd.notna(r["ecart_sensi"]) and abs(r["ecart_sensi"]) > tol_sensi:
                    r["statut"] = "sensi"
                else:
                    r["statut"] = "ok"
        r["cause"] = _cause(r)
        r["statut_lib"] = STATUTS[r["statut"]]
        lignes.append(r)
    df = pd.DataFrame(lignes)
    for c in ("valo_calcul", "sensi_calcul", "duration_calcul", "ecart_mad", "ecart_pb", "ecart_sensi", "ecart_duration",
              "taux", "crd", "type_lib", "emetteur", "echeance", "ech_eff", "base_coupon", "tf", "prime", "nominal", "grace"):
        if c not in df.columns:
            df[c] = np.nan
    return df


@lru_cache(maxsize=8)
def _comparer_cache(cle: str, valo_iso: str, courbe_iso: str, tol_prix: float, tol_sensi: float,
                    mtime_base: int, version_db: int) -> tuple[pd.DataFrame, str]:
    export = _EXPORTS[cle]
    courbe, info = SW.courbe_en_base(courbe_iso)
    if courbe.empty:
        return pd.DataFrame(), info
    return _comparer(export, date.fromisoformat(valo_iso), courbe, tol_prix, tol_sensi), info


_EXPORTS: dict[str, pd.DataFrame] = {}


def enregistrer_export(contenu: bytes, nom: str) -> tuple[str, pd.DataFrame]:
    """Lit l'export et le garde en mémoire (clé = empreinte du fichier)."""
    cle = hashlib.md5(contenu).hexdigest()
    if cle not in _EXPORTS:
        _EXPORTS[cle] = lire_export(contenu, nom)
    return cle, _EXPORTS[cle]


def comparer(cle: str, valo_iso: str, courbe_iso: str, tol_prix: float, tol_sensi: float) -> tuple[pd.DataFrame, str]:
    b = BT.CHEMIN.stat().st_mtime_ns if BT.CHEMIN.exists() else 0
    with SW._VERROU:
        return _comparer_cache(cle, valo_iso, courbe_iso, float(tol_prix), float(tol_sensi), b, db.version_base())


def resume(df: pd.DataFrame) -> dict:
    comp = df[df["statut"].isin(("ok",) + MAL_PRICES)]
    return {
        "n": len(df), "comparees": len(comp), "ok": int((df["statut"] == "ok").sum()),
        "prix": int((df["statut"] == "prix").sum()), "sensi": int((df["statut"] == "sensi").sum()),
        "non_comparees": int(df["statut"].isin(NON_COMPAREES).sum()),
        "taux_ok": (df["statut"] == "ok").sum() / len(comp) if len(comp) else np.nan,
        "ecart_abs_total": float(comp["ecart_mad"].abs().sum()) if len(comp) else 0.0,
        "ecart_med_pb": float(comp["ecart_pb"].abs().median()) if len(comp) else np.nan,
    }


def par_categorie(df: pd.DataFrame) -> pd.DataFrame:
    comp = df[df["statut"].isin(("ok",) + MAL_PRICES)]
    g = df.groupby("categorie")
    t = pd.DataFrame({
        "Lignes": g.size(),
        "Comparées": comp.groupby("categorie").size(),
        "Conformes": df[df["statut"] == "ok"].groupby("categorie").size(),
        "Écarts de prix": df[df["statut"] == "prix"].groupby("categorie").size(),
        "Écarts de sensi": df[df["statut"] == "sensi"].groupby("categorie").size(),
        "Non comparées": df[df["statut"].isin(NON_COMPAREES)].groupby("categorie").size(),
        "Écart médian (pb)": comp.groupby("categorie")["ecart_pb"].apply(lambda x: x.abs().median()),
        "Écart max (pb)": comp.groupby("categorie")["ecart_pb"].apply(lambda x: x.abs().max()),
    }).fillna({"Comparées": 0, "Conformes": 0, "Écarts de prix": 0, "Écarts de sensi": 0, "Non comparées": 0})
    t.loc["Total"] = [len(df), len(comp), (df["statut"] == "ok").sum(), (df["statut"] == "prix").sum(),
                      (df["statut"] == "sensi").sum(), df["statut"].isin(NON_COMPAREES).sum(),
                      comp["ecart_pb"].abs().median() if len(comp) else np.nan,
                      comp["ecart_pb"].abs().max() if len(comp) else np.nan]
    return t
