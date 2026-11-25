"""
Base historique de la courbe des taux BDT (SQLite).

- Historique des ténors (migré une fois depuis histo.xlsx, qui n'est plus utilisé ensuite).
- Chaque courbe téléchargée depuis BKAM y est ajoutée directement :
  taux des ténors + points de marché bruts (pour pouvoir tout recalculer plus tard).
- Historique des décisions de taux directeur de Bank Al-Maghrib.
- Export d'une période en Excel pour la communication.

Fichier de base : histo_courbe.db, dans le même dossier que ce script.
"""

from __future__ import annotations

import io
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DOSSIER = Path(__file__).resolve().parent
DB_PATH = DOSSIER / "histo_courbe.db"
EXCEL_PATH = DOSSIER / "histo.xlsx"                 # migration initiale uniquement
TD_EXCEL_PATH = DOSSIER / "tauxdirecteur.xlsx"      # import initial du taux directeur

# Même ordre et mêmes libellés que COURBE.TENORS
TENORS = {
    "13s": 91, "26s": 182, "52s": 364, "2ans": 731, "5ans": 1826,
    "10ans": 3653, "15ans": 5479, "20ans": 7305, "30ans": 10958,
}
TENOR_COLS = list(TENORS)

# Variantes acceptées dans les en-têtes de histo.xlsx (après normalisation)
_ALIAS = {
    "13s": "13s", "13sem": "13s", "13semaines": "13s",
    "26s": "26s", "26sem": "26s", "26semaines": "26s",
    "52s": "52s", "52sem": "52s", "52semaines": "52s",
    "2ans": "2ans", "2an": "2ans", "2y": "2ans",
    "5ans": "5ans", "5an": "5ans", "5y": "5ans",
    "10ans": "10ans", "10an": "10ans", "10y": "10ans",
    "15ans": "15ans", "15an": "15ans", "15y": "15ans",
    "20ans": "20ans", "20an": "20ans", "20y": "20ans",
    "30ans": "30ans", "30an": "30ans", "30y": "30ans",
}


# ---------------------------------------------------------------------------
# Connexion et schéma
# ---------------------------------------------------------------------------

@contextmanager
def connexion(chemin: Path | str = DB_PATH):
    """Connexion SQLite : commit à la sortie, rollback en cas d'erreur, toujours fermée."""
    conn = sqlite3.connect(chemin)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialiser(conn: sqlite3.Connection) -> None:
    cols = ", ".join(f'"{t}" REAL' for t in TENOR_COLS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS tenors (
            date   TEXT PRIMARY KEY,          -- AAAA-MM-JJ
            {cols},
            source TEXT NOT NULL,             -- 'excel' ou 'bkam'
            maj    TEXT NOT NULL              -- horodatage de l'écriture
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS courbe_bkam (
            date_courbe        TEXT NOT NULL,
            date_echeance      TEXT,
            montant            REAL,           -- colonne Transaction BKAM
            taux               REAL,
            date_valeur        TEXT,
            maturite_residuelle INTEGER
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_courbe_date ON courbe_bkam(date_courbe)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS taux_directeur (
            date                 TEXT PRIMARY KEY,   -- date de la décision (conseil BAM)
            taux                 REAL NOT NULL,      -- en %
            ratio_reserve        REAL,               -- ratio de réserve obligatoire, en %
            remuneration_reserve REAL,               -- rémunération de la réserve, en %
            maj                  TEXT NOT NULL
        )""")


def _iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _maintenant() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Import initial depuis histo.xlsx
# ---------------------------------------------------------------------------

def _normaliser(entete) -> str:
    s = unicodedata.normalize("NFKD", str(entete)).encode("ascii", "ignore").decode()
    return s.lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", "")


def lire_excel(chemin: Path | str = EXCEL_PATH) -> pd.DataFrame:
    """
    Lit histo.xlsx (1re feuille, en-têtes en ligne 1) et renvoie un DataFrame
    Date | 13s | ... | 30ans, taux en %.
    - colonne date : celle dont l'en-tête contient 'date', sinon la 1re colonne
    - ténors reconnus par leur libellé ('13s', '13 S', '30 ans', '30_ans', ...)
    - taux en décimal (0.0213) convertis automatiquement en % (2.13)
    """
    brut = pd.read_excel(chemin, sheet_name=0)
    noms = {c: _normaliser(c) for c in brut.columns}

    col_date = next((c for c, n in noms.items() if "date" in n), brut.columns[0])
    mapping = {c: _ALIAS[n] for c, n in noms.items() if n in _ALIAS}
    manquants = [t for t in TENOR_COLS if t not in mapping.values()]
    if manquants:
        raise ValueError(f"Ténors introuvables dans {Path(chemin).name} : {manquants}. "
                         f"En-têtes lus : {list(brut.columns)}")

    df = brut[[col_date, *mapping]].rename(columns={col_date: "Date", **mapping})
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"])
    for t in TENOR_COLS:
        df[t] = pd.to_numeric(df[t], errors="coerce")
        if df[t].dropna().abs().median() < 1:          # taux en décimal -> %
            df[t] = df[t] * 100
    return (df.dropna(subset=TENOR_COLS, how="all")
              .drop_duplicates("Date", keep="last")
              .sort_values("Date")
              .reset_index(drop=True))


def importer_excel(conn: sqlite3.Connection, chemin: Path | str = EXCEL_PATH) -> int:
    """
    Ajoute à la base les dates de histo.xlsx qui n'y sont pas encore.
    Les dates déjà présentes (ex. téléchargées depuis BKAM) ne sont pas écrasées.
    Renvoie le nombre de dates ajoutées.
    """
    df = lire_excel(chemin)
    avant = conn.execute("SELECT COUNT(*) FROM tenors").fetchone()[0]
    cols = ", ".join(f'"{t}"' for t in TENOR_COLS)
    marques = ", ".join("?" * (len(TENOR_COLS) + 3))
    maj = _maintenant()
    conn.executemany(
        f"INSERT OR IGNORE INTO tenors (date, {cols}, source, maj) VALUES ({marques})",
        [(_iso(r.Date), *[None if pd.isna(v) else float(v) for v in r[TENOR_COLS]], "excel", maj)
         for _, r in df.iterrows()],
    )
    return conn.execute("SELECT COUNT(*) FROM tenors").fetchone()[0] - avant


# ---------------------------------------------------------------------------
# Écriture d'une courbe téléchargée
# ---------------------------------------------------------------------------

def enregistrer_courbe(conn: sqlite3.Connection, d, taux: dict[str, float],
                       brute: pd.DataFrame | None = None, source: str = "bkam") -> None:
    """Enregistre (ou remplace) les ténors d'une date et ses points de marché BKAM."""
    cols = ", ".join(f'"{t}"' for t in TENOR_COLS)
    marques = ", ".join("?" * (len(TENOR_COLS) + 3))
    conn.execute(
        f"INSERT OR REPLACE INTO tenors (date, {cols}, source, maj) VALUES ({marques})",
        (_iso(d), *[float(taux[t]) for t in TENOR_COLS], source, _maintenant()),
    )
    if brute is not None and len(brute):
        # Colonnes repérées par mot-clé : clean_names peut donner 'date_decheance'
        # ou 'date_d_echeance' selon la version de pyjanitor
        def col(mot):
            return next(c for c in brute.columns if mot in _normaliser(c))
        c_ech, c_tr, c_tx = col("echeance"), col("transaction"), col("taux")
        c_val, c_mat = col("valeur"), col("maturite")

        def dt(v):
            return _iso(v) if pd.notna(v) else None

        def nb(v, typ=float):
            return None if pd.isna(v) else typ(v)

        conn.execute("DELETE FROM courbe_bkam WHERE date_courbe = ?", (_iso(d),))
        conn.executemany(
            "INSERT INTO courbe_bkam VALUES (?, ?, ?, ?, ?, ?)",
            [(_iso(d), dt(r[c_ech]), nb(r[c_tr]), nb(r[c_tx]), dt(r[c_val]), nb(r[c_mat], int))
             for _, r in brute.iterrows()],
        )


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def lire_tenors(conn: sqlite3.Connection, d) -> dict | None:
    """Ténors d'une date : {'13s': 2.13, ..., 'source': 'bkam', 'maj': '...'} ou None."""
    cur = conn.execute("SELECT * FROM tenors WHERE date = ?", (_iso(d),))
    ligne = cur.fetchone()
    if ligne is None:
        return None
    return dict(zip([c[0] for c in cur.description], ligne))


def lire_courbe_bkam(conn: sqlite3.Connection, d) -> pd.DataFrame:
    """Points de marché BKAM stockés pour une date (vide si date importée d'Excel)."""
    df = pd.read_sql_query(
        "SELECT date_echeance, montant AS \"transaction\", taux, date_valeur, maturite_residuelle "
        "FROM courbe_bkam WHERE date_courbe = ? ORDER BY maturite_residuelle",
        conn, params=(_iso(d),))
    for c in ("date_echeance", "date_valeur"):
        df[c] = pd.to_datetime(df[c])
    return df


def lire_historique(conn: sqlite3.Connection, debut=None, fin=None) -> pd.DataFrame:
    """Date | 13s | ... | 30ans sur une période (bornes incluses)."""
    cols = ", ".join(f'"{t}"' for t in TENOR_COLS)
    sql, params = f"SELECT date AS Date, {cols} FROM tenors WHERE 1=1", []
    if debut is not None:
        sql += " AND date >= ?"; params.append(_iso(debut))
    if fin is not None:
        sql += " AND date <= ?"; params.append(_iso(fin))
    df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params)
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def statistiques(conn: sqlite3.Connection) -> dict:
    n, d1, d2 = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM tenors").fetchone()
    return {"nb_dates": n,
            "premiere": pd.Timestamp(d1) if d1 else None,
            "derniere": pd.Timestamp(d2) if d2 else None}


# ---------------------------------------------------------------------------
# Dates de référence
# ---------------------------------------------------------------------------

def _une_date(conn, sql: str, *params) -> pd.Timestamp | None:
    r = conn.execute(sql, params).fetchone()[0]
    return pd.Timestamp(r) if r else None


def date_veille(conn: sqlite3.Connection, d) -> pd.Timestamp | None:
    """
    J-1 : jour ouvré précédent (vendredi pour un lundi) s'il est en base,
    sinon dernière date en base antérieure à la date de valorisation.
    (Les deux règles reviennent à prendre la dernière date en base < d.)
    """
    return _une_date(conn, "SELECT MAX(date) FROM tenors WHERE date < ?", _iso(d))


def date_hebdo(conn: sqlite3.Connection, d) -> pd.Timestamp | None:
    """Première date en base de la semaine de d (lundi, ou le 1er jour disponible)."""
    lundi = pd.Timestamp(d) - timedelta(days=pd.Timestamp(d).weekday())
    return _une_date(conn, "SELECT MIN(date) FROM tenors WHERE date >= ? AND date <= ?",
                     _iso(lundi), _iso(d))


def date_ytd(conn: sqlite3.Connection, d) -> pd.Timestamp | None:
    """Dernière observation de l'année précédente."""
    fin_annee = date(pd.Timestamp(d).year - 1, 12, 31)
    return _une_date(conn, "SELECT MAX(date) FROM tenors WHERE date <= ?", _iso(fin_annee))


# ---------------------------------------------------------------------------
# Export Excel (communication)
# ---------------------------------------------------------------------------

def exporter_excel(df: pd.DataFrame, feuille: str = "Courbe") -> bytes:
    """Classeur .xlsx mis en forme (en-tête, formats de date et de taux, volets figés)."""
    from openpyxl.styles import Alignment, Font, PatternFill

    tampon = io.BytesIO()
    with pd.ExcelWriter(tampon, engine="openpyxl") as xl:
        df.to_excel(xl, sheet_name=feuille, index=False)
        ws = xl.sheets[feuille]
        entete = PatternFill("solid", fgColor="0B4A8B")
        for c in ws[1]:
            c.fill, c.font = entete, Font(bold=True, color="FFFFFF")
            c.alignment = Alignment(horizontal="center")
        for ligne in ws.iter_rows(min_row=2):
            for c in ligne:
                if c.column == 1:
                    c.number_format = "dd/mm/yyyy"
                else:
                    c.number_format = "0.000"
        ws.column_dimensions["A"].width = 13
        for col in ws.iter_cols(min_col=2, max_col=ws.max_column):
            ws.column_dimensions[col[0].column_letter].width = 9
        ws.freeze_panes = "B2"
    return tampon.getvalue()


# ---------------------------------------------------------------------------
# Taux directeur
# ---------------------------------------------------------------------------

def lire_excel_taux_directeur(chemin: Path | str = TD_EXCEL_PATH) -> pd.DataFrame:
    """
    Lit le fichier des décisions de taux directeur (ligne d'en-tête repérée par 'Date',
    même si des lignes vides la précèdent). Taux en décimal convertis en %.
    Colonnes renvoyées : date, taux, ratio_reserve, remuneration_reserve.
    """
    brut = pd.read_excel(chemin, sheet_name=0, header=None)
    ligne = next(i for i, v in brut.iloc[:, 0].items() if _normaliser(v) == "date")
    df = brut.iloc[ligne + 1:].copy()
    df.columns = [_normaliser(c) for c in brut.iloc[ligne]]

    def col(*mots):
        return next((c for c in df.columns if any(m in c for m in mots)), None)

    out = pd.DataFrame({
        "date": pd.to_datetime(df[col("date")], dayfirst=True, errors="coerce"),
        "taux": pd.to_numeric(df[col("tauxdirecteur", "taux")], errors="coerce"),
        "ratio_reserve": pd.to_numeric(df[col("ratio")], errors="coerce") if col("ratio") else None,
        "remuneration_reserve": pd.to_numeric(df[col("remuneration")], errors="coerce")
                                if col("remuneration") else None,
    }).dropna(subset=["date", "taux"])
    for c in ("taux", "ratio_reserve", "remuneration_reserve"):
        if out[c].notna().any() and out[c].abs().max() < 1:      # décimal -> %
            out[c] = out[c] * 100
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def enregistrer_taux_directeur(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Ajoute ou remplace des décisions (colonnes date, taux[, ratio_reserve, remuneration_reserve])."""
    maj = _maintenant()

    def v(r, c):
        x = r.get(c) if hasattr(r, "get") else None
        return None if x is None or pd.isna(x) else float(x)

    lignes = [(_iso(r["date"]), float(r["taux"]), v(r, "ratio_reserve"),
               v(r, "remuneration_reserve"), maj) for _, r in df.iterrows()]
    conn.executemany("INSERT OR REPLACE INTO taux_directeur VALUES (?, ?, ?, ?, ?)", lignes)
    return len(lignes)


def lire_taux_directeur(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT date, taux, ratio_reserve, remuneration_reserve "
                           "FROM taux_directeur ORDER BY date", conn)
    df["date"] = pd.to_datetime(df["date"])
    return df


def taux_directeur_en_vigueur(conn: sqlite3.Connection, d=None) -> tuple[pd.Timestamp, float] | None:
    """Dernière décision à la date d (aujourd'hui par défaut) : (date de décision, taux)."""
    r = conn.execute("SELECT date, taux FROM taux_directeur WHERE date <= ? "
                     "ORDER BY date DESC LIMIT 1", (_iso(d or date.today()),)).fetchone()
    return (pd.Timestamp(r[0]), r[1]) if r else None


def taux_directeur_journalier(conn: sqlite3.Connection, dates) -> pd.Series:
    """Taux directeur en vigueur à chaque date (palier : dernière décision connue)."""
    td = lire_taux_directeur(conn)
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).sort_values()
    if td.empty:
        return pd.Series(index=idx, dtype=float, name="taux_directeur")
    s = td.set_index("date")["taux"]
    return s.reindex(s.index.union(idx)).ffill().reindex(idx).rename("taux_directeur")


def version_base(chemin: Path | str = DB_PATH) -> int:
    """Change à chaque écriture dans la base : sert à invalider les calculs en cache."""
    p = Path(chemin)
    return p.stat().st_mtime_ns if p.exists() else 0


def exporter_classeur(feuilles: dict[str, pd.DataFrame]) -> bytes:
    """Classeur .xlsx de plusieurs feuilles, mis en forme (étude statistique, etc.)."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    tampon = io.BytesIO()
    entete = PatternFill("solid", fgColor="0B4A8B")
    with pd.ExcelWriter(tampon, engine="openpyxl") as xl:
        for nom, df in feuilles.items():
            nom = nom[:31]
            df.to_excel(xl, sheet_name=nom, index=False)
            ws = xl.sheets[nom]
            for c in ws[1]:
                c.fill, c.font = entete, Font(bold=True, color="FFFFFF")
                c.alignment = Alignment(horizontal="center", wrap_text=True)
            for j, col in enumerate(df.columns, start=1):
                serie = df[col]
                fmt = ("dd/mm/yyyy" if pd.api.types.is_datetime64_any_dtype(serie)
                       else "0.000" if pd.api.types.is_float_dtype(serie) else None)
                if fmt:
                    for (c,) in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                        c.number_format = fmt
                largeur = max(len(str(col)), *(len(f"{v}") for v in serie.head(200))) if len(serie) else len(str(col))
                ws.column_dimensions[get_column_letter(j)].width = min(max(10, largeur + 2), 40)
            ws.freeze_panes = "B2"
    return tampon.getvalue()
