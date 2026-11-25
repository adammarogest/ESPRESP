"""
Trésorerie : position globale du jour et opérations qui l'impactent (base SQLite tresorerie.db).

Convention de signe : + = excédent à placer / entrée de trésorerie ; − = besoin à emprunter / sortie.
- Position de base du jour : saisie par l'utilisateur (reprise du système d'information), modifiable
  à tout moment ; chaque saisie est historisée, la dernière fait foi.
- Opérations, par catégorie :
    contreparties   Emprunt (+) / Placement (−) auprès d'une SDG ou d'une salle de marché
                    (taux et échéance facultatifs : le dénouement est programmé à l'échéance)
    règlements / livraisons   Encaissement (+) / Décaissement (−), instantané ou programmé
    souscriptions / rachats   Souscription (+) / Rachat (−)
    ferme           Vente (+) / Achat (−) ; à l'exécution, montant réel = nombre de bons × prix
    ajustements     écart ± avec le système d'information
- Position exécutée = base + opérations exécutées du jour ;
  position prévisionnelle = base + toutes les opérations du jour (exécutées ou non).
Rien n'est effacé : une opération annulée reste dans l'historique, chaque action est journalisée.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CHEMIN = Path(__file__).resolve().parent / "tresorerie.db"

SDG = ["AD CAPITAL", "AFG", "ALPHAVEST", "ATLAS", "BMCE GESTION", "CAPITAL TRUST", "CDG GESTION", "CFG GESTION",
       "CIH CAPITAL", "REDMED ASSET MANAGEMENT", "SAHAM MANAGEMENT", "STERLING", "UPLINE", "VALORIS",
       "WAFA GESTION", "WINEO"]
SALLES = ["CDM", "BCP", "CFG", "BOA", "CAM", "ALBARID", "CIH", "CDG CAPITAL"]
TYPES_CONTREPARTIE = {"SDG": SDG, "Salle de marché": SALLES}
FONDS = ["FMGC", "FCM", "FML", "FCOB", "FMS"]

CATEGORIES = {
    "contrepartie": "Contreparties", "rl": "Règlements / livraisons", "sousrachat": "Souscriptions / rachats",
    "ferme": "Ferme", "ajustement": "Ajustements", "denouement": "Dénouements",
}
# type d'opération -> (catégorie, sens)
TYPES = {
    "Emprunt": ("contrepartie", 1), "Placement": ("contrepartie", -1),
    "Encaissement": ("rl", 1), "Décaissement": ("rl", -1),
    "Souscription": ("sousrachat", 1), "Rachat": ("sousrachat", -1),
    "Achat": ("ferme", -1), "Vente": ("ferme", 1),
    "Ajustement": ("ajustement", 1),
}
TYPES_PAR_CATEGORIE = {c: [t for t, (cc, _) in TYPES.items() if cc == c] for c in CATEGORIES}

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_jour TEXT NOT NULL,
    montant REAL NOT NULL,
    saisi_le TEXT NOT NULL,
    commentaire TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    categorie TEXT NOT NULL,
    type_op TEXT NOT NULL,
    sens INTEGER NOT NULL,
    date_valeur TEXT NOT NULL,
    saisi_le TEXT NOT NULL,
    modifie_le TEXT,
    montant REAL NOT NULL,
    contrepartie TEXT,
    type_contrepartie TEXT,
    titre TEXT,
    libelle TEXT,
    fonds TEXT,
    taux REAL,
    echeance TEXT,
    execute INTEGER NOT NULL DEFAULT 0,
    execute_le TEXT,
    nb_bons REAL,
    prix REAL,
    commentaire TEXT,
    annule INTEGER NOT NULL DEFAULT 0,
    annule_le TEXT
);
CREATE INDEX IF NOT EXISTS ix_op_date ON operations(date_valeur);
CREATE INDEX IF NOT EXISTS ix_op_ech ON operations(echeance);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage TEXT NOT NULL,
    operation_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT
);
"""


def maintenant() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connexion(chemin: Path | str = CHEMIN):
    conn = sqlite3.connect(str(chemin))
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _journal(conn, operation_id, action: str, detail: str = "") -> None:
    conn.execute("INSERT INTO journal (horodatage, operation_id, action, detail) VALUES (?, ?, ?, ?)",
                 (maintenant(), operation_id, action, detail))


def _iso(d) -> str | None:
    if d in (None, ""):
        return None
    return pd.Timestamp(d).date().isoformat()


def _mad(v) -> str:
    return f"{v:,.2f}".replace(",", " ")


# ---------------------------------------------------------------------------
# Écritures
# ---------------------------------------------------------------------------

def saisir_base(jour, montant: float, commentaire: str = "") -> None:
    """Nouvelle position de base du jour (historisée ; la dernière saisie fait foi)."""
    with connexion() as conn:
        conn.execute("INSERT INTO positions_base (date_jour, montant, saisi_le, commentaire) VALUES (?, ?, ?, ?)",
                     (_iso(jour), float(montant), maintenant(), commentaire or None))
        _journal(conn, None, "Position de base", f"{_iso(jour)} : {_mad(float(montant))}" + (f" ({commentaire})" if commentaire else ""))


def ajouter(type_op: str, date_valeur, montant: float, **champs) -> int:
    """Nouvelle opération ; renvoie son identifiant. Pour un ajustement, le montant est signé."""
    categorie, sens = TYPES[type_op]
    montant = float(montant)
    if type_op == "Ajustement":
        sens, montant = (1 if montant >= 0 else -1), abs(montant)
    if montant <= 0 and type_op != "Ajustement":
        raise ValueError("le montant doit être strictement positif (le sens est donné par le type d'opération)")
    execute = 1 if champs.get("execute") or type_op == "Ajustement" else 0   # un ajustement corrige le SI : acquis
    cols = {"categorie": categorie, "type_op": type_op, "sens": sens, "date_valeur": _iso(date_valeur),
            "saisi_le": maintenant(), "montant": montant, "execute": execute,
            "execute_le": maintenant() if execute else None}
    for c in ("contrepartie", "type_contrepartie", "titre", "libelle", "fonds", "taux", "nb_bons", "prix", "commentaire"):
        v = champs.get(c)
        cols[c] = None if v in ("", None) else v
    cols["echeance"] = _iso(champs.get("echeance"))
    with connexion() as conn:
        cur = conn.execute(f"INSERT INTO operations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           tuple(cols.values()))
        oid = cur.lastrowid
        _journal(conn, oid, "Création", f"{type_op} {_mad(sens * montant)} valeur {cols['date_valeur']}"
                 + (" (exécutée)" if execute else ""))
    return oid


MODIFIABLES = {"execute", "nb_bons", "prix", "montant", "taux", "echeance", "commentaire", "date_valeur"}


def modifier(oid: int, **champs) -> None:
    """Modifie une opération (exécution, nombre de bons, prix, montant…) ; chaque changement est journalisé."""
    champs = {k: v for k, v in champs.items() if k in MODIFIABLES}
    if not champs:
        return
    with connexion() as conn:
        avant = conn.execute(f"SELECT {', '.join(champs)} FROM operations WHERE id = ?", (oid,)).fetchone()
        if avant is None:
            return
        sets, vals, details = [], [], []
        for (k, v), a in zip(champs.items(), avant):
            if k in ("echeance", "date_valeur"):
                v = _iso(v)
            if k == "execute":
                v = 1 if v in (1, True, "Oui", "oui") else 0
            if k in ("nb_bons", "prix", "montant", "taux"):
                v = None if v in ("", None) else float(v)
            if v == a:
                continue
            sets.append(f"{k} = ?")
            vals.append(v)
            details.append(f"{k} : {a} → {v}")
            if k == "execute":
                sets.append("execute_le = ?")
                vals.append(maintenant() if v else None)
        if not sets:
            return
        sets.append("modifie_le = ?")
        vals.append(maintenant())
        conn.execute(f"UPDATE operations SET {', '.join(sets)} WHERE id = ?", (*vals, oid))
        action = "Exécution" if champs.get("execute") in (1, True, "Oui") and len(details) == 1 and details[0].startswith("execute") \
            else "Modification"
        _journal(conn, oid, action, " ; ".join(details))


def annuler(oids: list[int]) -> int:
    with connexion() as conn:
        n = 0
        for oid in oids:
            cur = conn.execute("UPDATE operations SET annule = 1, annule_le = ? WHERE id = ? AND annule = 0", (maintenant(), oid))
            if cur.rowcount:
                _journal(conn, oid, "Annulation", "")
                n += 1
    return n


# ---------------------------------------------------------------------------
# Lectures et calculs
# ---------------------------------------------------------------------------

def lire_operations(debut=None, fin=None, categorie: str | None = None, annulees: bool = False) -> pd.DataFrame:
    sql, args = "SELECT * FROM operations WHERE 1 = 1", []
    if not annulees:
        sql += " AND annule = 0"
    if categorie:
        sql += " AND categorie = ?"
        args.append(categorie)
    if debut:
        sql += " AND date_valeur >= ?"
        args.append(_iso(debut))
    if fin:
        sql += " AND date_valeur <= ?"
        args.append(_iso(fin))
    with connexion() as conn:
        df = pd.read_sql(sql + " ORDER BY date_valeur, saisi_le, id", conn, params=args)
    return enrichir(df)


def montant_retenu(r) -> float:
    """Ferme exécutée avec nombre de bons et prix : montant réel = nb × prix ; sinon montant saisi."""
    if r["categorie"] == "ferme" and r["execute"] and pd.notna(r.get("nb_bons")) and pd.notna(r.get("prix")):
        return float(r["nb_bons"]) * float(r["prix"])
    return float(r["montant"])


def enrichir(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for c in ("montant_retenu", "impact"):
            df[c] = pd.Series(dtype=float)
        return df
    df = df.copy()
    df["montant_retenu"] = df.apply(montant_retenu, axis=1)
    df["impact"] = df["sens"] * df["montant_retenu"]
    df["execute"] = df["execute"].astype(bool)
    return df


def denouements(jour=None, debut=None, fin=None) -> pd.DataFrame:
    """Flux de dénouement des emprunts / placements à échéance (capital + intérêts, base 360)."""
    sql, args = ("SELECT * FROM operations WHERE annule = 0 AND categorie = 'contrepartie' AND echeance IS NOT NULL", [])
    if jour:
        sql += " AND echeance = ?"
        args.append(_iso(jour))
    if debut:
        sql += " AND echeance >= ?"
        args.append(_iso(debut))
    if fin:
        sql += " AND echeance <= ?"
        args.append(_iso(fin))
    with connexion() as conn:
        src = pd.read_sql(sql, conn, params=args)
    if src.empty:
        return enrichir(src.assign(categorie=pd.Series(dtype=str)))
    j = (pd.to_datetime(src["echeance"]) - pd.to_datetime(src["date_valeur"])).dt.days
    interets = src["montant"] * src["taux"].fillna(0) / 100 * j / 360
    d = src.copy()
    d["origine"] = src["id"]
    d["id"] = -src["id"]                                   # identifiant négatif : flux calculé
    d["categorie"] = "denouement"
    d["type_op"] = np.where(src["sens"] > 0, "Remboursement d'emprunt", "Retour de placement")
    d["sens"] = -src["sens"]
    d["date_valeur"] = src["echeance"]
    d["montant"] = src["montant"] + interets
    d["libelle"] = [f"Dénouement de l'opération n° {i} du {pd.Timestamp(v):%d/%m/%Y}" for i, v in zip(src["id"], src["date_valeur"])]
    d["saisi_le"] = d["date_valeur"] + " 00:00:00"
    return enrichir(d)


def base_du_jour(jour) -> dict | None:
    with connexion() as conn:
        r = conn.execute("SELECT montant, saisi_le, commentaire FROM positions_base WHERE date_jour = ? "
                         "ORDER BY saisi_le DESC, id DESC LIMIT 1", (_iso(jour),)).fetchone()
    return {"montant": r[0], "saisi_le": r[1], "commentaire": r[2]} if r else None


def historique_base(jour=None, n: int = 50) -> pd.DataFrame:
    with connexion() as conn:
        if jour:
            return pd.read_sql("SELECT * FROM positions_base WHERE date_jour = ? ORDER BY saisi_le DESC, id DESC",
                               conn, params=[_iso(jour)])
        return pd.read_sql("SELECT * FROM positions_base ORDER BY saisi_le DESC, id DESC LIMIT ?", conn, params=[n])


def operations_du_jour(jour) -> pd.DataFrame:
    ops = lire_operations(jour, jour)
    den = denouements(jour=jour)
    if not den.empty:
        den["execute"] = den["execute"].astype(bool)
    tout = pd.concat([den, ops], ignore_index=True) if not den.empty else ops
    return tout.sort_values(["saisi_le", "id"]).reset_index(drop=True) if not tout.empty else tout


def position(jour) -> dict:
    """Position de base, exécutée et prévisionnelle du jour, avec l'impact de chaque catégorie."""
    b = base_du_jour(jour)
    base = b["montant"] if b else 0.0
    ops = operations_du_jour(jour)
    ex = ops[ops["execute"]] if not ops.empty else ops
    par_cat = (ops.groupby("categorie")["impact"].sum() if not ops.empty else pd.Series(dtype=float))
    par_cat_ex = (ex.groupby("categorie")["impact"].sum() if not ex.empty else pd.Series(dtype=float))
    prev = base + (ops["impact"].sum() if not ops.empty else 0.0)
    exe = base + (ex["impact"].sum() if not ex.empty else 0.0)
    return {"jour": _iso(jour), "base": base, "base_saisie": b, "executee": exe, "previsionnelle": prev,
            "en_attente": prev - exe, "n_ops": len(ops), "n_executees": len(ex),
            "par_categorie": par_cat, "par_categorie_executee": par_cat_ex}


def impact_chronologique(jour) -> pd.DataFrame:
    """Opérations du jour dans l'ordre de saisie, avec la position après chacune (prévisionnelle et exécutée)."""
    b = base_du_jour(jour)
    base = b["montant"] if b else 0.0
    ops = operations_du_jour(jour)
    if ops.empty:
        return ops
    ops = ops.copy()
    ops["position_previsionnelle"] = base + ops["impact"].cumsum()
    ops["position_executee"] = base + (ops["impact"] * ops["execute"]).cumsum()
    return ops


def a_venir(jour, jours: int = 30) -> pd.DataFrame:
    """Opérations programmées (et dénouements) après le jour affiché."""
    debut = pd.Timestamp(jour) + timedelta(days=1)
    fin = pd.Timestamp(jour) + timedelta(days=jours)
    ops = lire_operations(debut, fin)
    den = denouements(debut=debut, fin=fin)
    tout = pd.concat([den, ops], ignore_index=True) if not den.empty else ops
    return tout.sort_values(["date_valeur", "saisi_le"]).reset_index(drop=True) if not tout.empty else tout


def cloture_precedente(jour) -> tuple[str, float] | None:
    """Position prévisionnelle de clôture du dernier jour antérieur ayant une position de base."""
    with connexion() as conn:
        r = conn.execute("SELECT MAX(date_jour) FROM positions_base WHERE date_jour < ?", (_iso(jour),)).fetchone()
    if not r or not r[0]:
        return None
    return r[0], position(r[0])["previsionnelle"]


def encours_contreparties(jour) -> pd.DataFrame:
    """Emprunts / placements en cours au jour (valeur ≤ jour < échéance ; sans échéance : jour de valeur seul)."""
    j = _iso(jour)
    with connexion() as conn:
        df = pd.read_sql("SELECT * FROM operations WHERE annule = 0 AND categorie = 'contrepartie' AND date_valeur <= ? "
                         "AND ((echeance IS NOT NULL AND echeance > ?) OR (echeance IS NULL AND date_valeur = ?))",
                         conn, params=[j, j, j])
    if df.empty:
        return pd.DataFrame(columns=["Type", "Emprunté", "Placé", "Net", "Opérations"])
    df["emprunte"] = np.where(df["sens"] > 0, df["montant"], 0.0)
    df["place"] = np.where(df["sens"] < 0, df["montant"], 0.0)
    g = df.groupby(["contrepartie"])
    t = pd.DataFrame({"Type": g["type_contrepartie"].first(), "Emprunté": g["emprunte"].sum(), "Placé": g["place"].sum(),
                      "Opérations": g.size()})
    t["Net"] = t["Emprunté"] - t["Placé"]
    return t[["Type", "Emprunté", "Placé", "Net", "Opérations"]].sort_values("Net")


def volumes_contreparties(debut, fin) -> pd.DataFrame:
    df = lire_operations(debut, fin, "contrepartie")
    if df.empty:
        return pd.DataFrame(columns=["Type", "Emprunts", "Placements", "Opérations", "Dernière opération"])
    g = df.groupby("contrepartie")
    return pd.DataFrame({
        "Type": g["type_contrepartie"].first(),
        "Emprunts": g.apply(lambda x: x.loc[x["sens"] > 0, "montant"].sum(), include_groups=False),
        "Placements": g.apply(lambda x: x.loc[x["sens"] < 0, "montant"].sum(), include_groups=False),
        "Opérations": g.size(),
        "Dernière opération": g["date_valeur"].max(),
    }).sort_values("Opérations", ascending=False)


def lire_journal(n: int = 200) -> pd.DataFrame:
    with connexion() as conn:
        return pd.read_sql("SELECT * FROM journal ORDER BY id DESC LIMIT ?", conn, params=[n])


def besoin(montant: float) -> str:
    if abs(montant) < 0.005:
        return "Position équilibrée"
    return f"À placer {_mad(montant)}" if montant > 0 else f"À emprunter {_mad(-montant)}"
