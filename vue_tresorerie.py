"""
Vues de trésorerie : bandeau de position et gabarit des fenêtres d'opérations
(contreparties, règlements / livraisons, souscriptions / rachats, ferme).
"""

from __future__ import annotations

import time

from datetime import date

import numpy as np
import pandas as pd
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme, Sign
from dash.exceptions import PreventUpdate

import base_titre as BT
import tresorerie as TR
from composants import signaux, carte, etiquette, fdate, kpi, note, segment, toast
import theme as T

F0 = Format(precision=0, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2 = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2S = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ", sign=Sign.positive)
F3 = Format(precision=3, scheme=Scheme.fixed)


def mad(v, signe=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return (f"{v:+,.2f}" if signe else f"{v:,.2f}").replace(",", " ")


def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def jour_fr(id_, valeur=None, clearable=False):
    return dcc.DatePickerSingle(id=id_, date=valeur, display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA",
                                first_day_of_week=1, month_format="MMMM YYYY", clearable=clearable, className="date-fr")


def nombre(id_, **kw):
    return dcc.Input(id=id_, type="number", step="any", className="input-date input-champ", **kw)


def texte(id_, **kw):
    return dcc.Input(id=id_, type="text", className="input-date input-champ", **kw)


def cls_montant(v):
    return T.VERT if v > 0 else T.ROUGE if v < 0 else None


def kpis_position(jour, impact_categorie: str | None = None) -> list:
    """Indicateurs de position du jour (et impact de la catégorie de la fenêtre)."""
    p = TR.position(jour)
    b = p["base_saisie"]
    cartes = [
        kpi("Position de base", mad(p["base"], True),
            f"saisie le {pd.Timestamp(b['saisi_le']):%d/%m %H:%M}" if b else "aucune saisie pour ce jour",
            None if b else T.ROUGE),
        kpi("Position exécutée", mad(p["executee"], True), f"{p['n_executees']} opération(s) exécutée(s)"),
        kpi("En attente", mad(p["en_attente"], True), f"{p['n_ops'] - p['n_executees']} non exécutée(s)"),
        kpi("Position prévisionnelle", mad(p["previsionnelle"], True), "base + toutes les opérations du jour",
            cls_montant(p["previsionnelle"])),
        kpi("Besoin", mad(abs(p["previsionnelle"])),
            "à placer" if p["previsionnelle"] > 0.005 else "à emprunter" if p["previsionnelle"] < -0.005 else "position équilibrée",
            cls_montant(p["previsionnelle"])),
    ]
    if impact_categorie:
        imp = float(p["par_categorie"].get(impact_categorie, 0.0))
        cartes.append(kpi(f"Impact {TR.CATEGORIES[impact_categorie].lower()}", mad(imp, True), "sur la journée",
                          cls_montant(imp)))
    else:
        den = float(p["par_categorie"].get("denouement", 0.0))
        cartes.append(kpi("Dénouements du jour", mad(den, True), "emprunts / placements à échéance", cls_montant(den)))
    return cartes


def message_impact(jour, avant: float) -> str:
    apres = TR.position(jour)["previsionnelle"]
    return (f"Position prévisionnelle du {fdate(jour)} : {mad(avant, True)} → {mad(apres, True)} "
            f"(impact {mad(apres - avant, True)}) · {TR.besoin(apres)}.")


STYLE_TABLE = dict(
    style_as_list_view=True, style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600, "fontSize": "0.6rem",
                  "letterSpacing": "0.1em", "textTransform": "uppercase", "border": "none",
                  "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.55rem", "textAlign": "right",
                  "whiteSpace": "normal"},
    style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.78rem",
                "padding": "0.42rem 0.55rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
    css=[{"selector": ".Select-menu-outer", "rule": "display: block !important; z-index: 30;"}],
)
TEXTE = ("horodatage_txt", "action", "date_txt", "saisi_txt", "type_op", "statut", "contrepartie", "type_contrepartie", "titre", "libelle", "fonds",
         "echeance_txt", "execute_txt", "commentaire", "categorie_lib", "detail")


def style_texte(colonnes) -> dict:
    ids = [c["id"] for c in colonnes]
    return dict(
        style_header_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ids if c in TEXTE],
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ids if c in TEXTE] + [
            {"if": {"column_id": "libelle"}, "maxWidth": "240px", "overflow": "hidden", "textOverflow": "ellipsis"},
            {"if": {"column_id": "commentaire"}, "maxWidth": "220px", "overflow": "hidden", "textOverflow": "ellipsis"},
        ] + [{"if": {"column_id": c}, "minWidth": "115px"} for c in ids if c in ("montant", "prix", "montant_retenu",
                                                                                  "impact", "nb_bons")],
    )


def lignes_table(df: pd.DataFrame, jour) -> list[dict]:
    if df.empty:
        return []
    d = df.copy()
    j = TR._iso(jour)
    d["date_txt"] = pd.to_datetime(d["date_valeur"]).dt.strftime("%d/%m/%Y")
    d["saisi_txt"] = pd.to_datetime(d["saisi_le"]).dt.strftime("%d/%m/%Y %H:%M")
    if "echeance" in d:
        d["echeance_txt"] = pd.to_datetime(d["echeance"], errors="coerce").dt.strftime("%d/%m/%Y")
    d["execute_txt"] = np.where(d["execute"], "Oui", "Non")
    statut = np.where(d["execute"], "Exécutée", np.where(d["date_valeur"] > j, "Programmée", "En attente"))
    if "annule" in d:
        statut = np.where(d["annule"] == 1, "Annulée", statut)
    d["statut"] = statut
    d["categorie_lib"] = d["categorie"].map(TR.CATEGORIES)
    return d.replace({np.nan: None}).to_dict("records")


# ---------------------------------------------------------------------------
# Gabarit des fenêtres d'opérations
# ---------------------------------------------------------------------------

CONFIG = {
    "contrepartie": {
        "titre": "Contreparties", "sous_titre": "Emprunts (+) et placements (−) auprès des SDG et des salles de marché",
        "colonnes": ["contrepartie", "type_contrepartie", "taux", "echeance_txt"],
    },
    "rl": {
        "titre": "Règlements / livraisons", "sous_titre": "Encaissements (+) et décaissements (−), instantanés ou programmés",
        "colonnes": ["titre", "libelle", "contrepartie"],
    },
    "sousrachat": {
        "titre": "Souscriptions / rachats", "sous_titre": "Souscriptions (+) et rachats (−)",
        "colonnes": ["fonds", "libelle"],
    },
    "ferme": {
        "titre": "Ferme", "sous_titre": "Achats (−) et ventes (+) ; à l'exécution, montant réel = nombre de bons × prix",
        "colonnes": ["titre", "libelle", "contrepartie", "nb_bons", "prix", "montant_retenu"],
    },
}

NOMS = {"contrepartie": "Contrepartie", "type_contrepartie": "Type", "taux": "Taux (%)", "echeance_txt": "Échéance",
        "titre": "Titre", "libelle": "Libellé", "fonds": "Fonds", "nb_bons": "Nb de bons", "prix": "Prix",
        "montant_retenu": "Montant réel"}


def colonnes_table(categorie: str) -> list[dict]:
    cols = [{"id": "id", "name": "N°", "type": "numeric", "editable": False},
            {"id": "date_txt", "name": "Date de valeur", "editable": False},
            {"id": "type_op", "name": "Opération", "editable": False}]
    for c in CONFIG[categorie]["colonnes"]:
        d = {"id": c, "name": NOMS[c], "editable": c in ("nb_bons", "prix", "taux")}
        if c in ("nb_bons",):
            d.update(type="numeric", format=F0)
        elif c in ("prix", "montant_retenu"):
            d.update(type="numeric", format=F2)
        elif c == "taux":
            d.update(type="numeric", format=F3)
        cols.append(d)
    cols += [
        {"id": "montant", "name": "Montant estimé" if categorie == "ferme" else "Montant", "type": "numeric",
         "format": F2, "editable": True},
        {"id": "impact", "name": "Impact", "type": "numeric", "format": F2S, "editable": False},
        {"id": "execute_txt", "name": "Exécutée ✎", "presentation": "dropdown", "editable": True},
        {"id": "statut", "name": "Statut", "editable": False},
        {"id": "commentaire", "name": "Commentaire ✎", "editable": True},
        {"id": "saisi_txt", "name": "Saisie le", "editable": False},
    ]
    return cols


def formulaire(categorie: str, px: str) -> list:
    aujourdhui = date.today().isoformat()
    types = TR.TYPES_PAR_CATEGORIE[categorie]
    commun_debut = [
        champ("Opération", segment(f"{px}-type", [(t, t) for t in types], types[0])),
        champ("Date de valeur", jour_fr(f"{px}-date", aujourdhui)),
    ]
    montant_lib = "Montant estimé (MAD)" if categorie == "ferme" else "Montant (MAD)"
    commun_fin = [
        champ(montant_lib, nombre(f"{px}-montant", min=0, placeholder="positif ; le sens vient de l'opération")),
        champ("Commentaire", texte(f"{px}-commentaire", placeholder="facultatif"), "champ-large"),
        champ("Statut", dcc.Checklist(id=f"{px}-execute", className="case",
                                      options=[{"label": "Exécutée", "value": "oui"}], value=[])),
    ]
    if categorie == "contrepartie":
        specifique = [
            champ("Type de contrepartie", segment(f"{px}-typecp", [(k, k) for k in TR.TYPES_CONTREPARTIE], "SDG")),
            champ("Contrepartie", dcc.Dropdown(id=f"{px}-cp", options=TR.SDG, placeholder="Choisir", className="liste",
                                               clearable=False), "champ-large"),
            champ("Taux (%)", nombre(f"{px}-taux", placeholder="facultatif")),
            champ("Échéance", jour_fr(f"{px}-echeance", None, clearable=True)),
        ]
    elif categorie == "rl":
        specifique = [
            champ("Titre (code)", dcc.Input(id=f"{px}-titre", type="text", list=f"{px}-codes", debounce=True,
                                            autoComplete="off", className="input-date input-champ input-court",
                                            placeholder="facultatif")),
            champ("Libellé", texte(f"{px}-libelle", placeholder="ex. livraison BDT 5 ans"), "champ-large"),
            champ("Contrepartie", dcc.Dropdown(id=f"{px}-cp", options=TR.SALLES + TR.SDG, placeholder="facultatif",
                                               className="liste"), "champ-large"),
        ]
    elif categorie == "sousrachat":
        specifique = [
            champ("Fonds", dcc.Dropdown(id=f"{px}-fonds", options=TR.FONDS, placeholder="Choisir", className="liste", clearable=False)),
            champ("Client / porteur", texte(f"{px}-libelle", placeholder="facultatif"), "champ-large"),
        ]
    else:  # ferme
        specifique = [
            champ("Titre (code)", dcc.Input(id=f"{px}-titre", type="text", list=f"{px}-codes", debounce=True,
                                            autoComplete="off", className="input-date input-champ input-court")),
            champ("Contrepartie", dcc.Dropdown(id=f"{px}-cp", options=TR.SALLES + TR.SDG, placeholder="facultatif",
                                               className="liste"), "champ-large"),
            champ("Nombre de bons", nombre(f"{px}-nb", min=0, placeholder="à l'exécution")),
            champ("Prix (MAD)", nombre(f"{px}-prix", min=0, placeholder="à l'exécution")),
        ]
    return commun_debut + specifique + commun_fin


def construire(categorie: str, jour_id: str = "tg-jour", msg_id: str = "tg-msg") -> html.Div:
    """
    Contenu d'un onglet du tableau de bord Trésorerie : saisie, tableau des opérations (et encours pour
    les contreparties). La journée affichée et la ligne de message sont celles du tableau de bord.
    """
    px = f"tr-{categorie}"
    cfg = CONFIG[categorie]
    colonnes = colonnes_table(categorie)
    filtres = [("Journée", "jour"), ("Programmées", "programmees"), ("Historique", "historique"), ("Annulées", "annulees")]

    extra = []
    if categorie == "contrepartie":
        extra = [html.Div(className="grille-2", children=[
            carte("Encours au jour", "emprunts et placements non échus", html.Div(id=f"{px}-encours", className="tableau-defile")),
            carte("Volumes depuis le 1er janvier", "par contrepartie", html.Div(id=f"{px}-volumes", className="tableau-defile")),
        ])]

    layout = html.Div(className="onglet-treso", children=[
        dcc.Download(id=f"{px}-dl"),
        html.Datalist(id=f"{px}-codes"),
        html.Div(className="carte", children=[
            html.Div(className="carte-tete", children=[etiquette("Nouvelle opération", None)]),
            html.Div(className="barre barre-interne formulaire-treso", children=formulaire(categorie, px)),
            html.Div(className="ligne-boutons", children=[
                html.Button("Enregistrer l'opération", id=f"{px}-ajouter", className="btn btn-primaire"),
                html.Button(f"Exporter l'historique", id=f"{px}-export", className="btn btn-lien")]),
        ]),
        carte("Opérations", "modifiez « Exécutée », le montant, le commentaire"
              + (", le nombre de bons et le prix" if categorie == "ferme" else "")
              + " directement dans le tableau ; cochez des lignes pour les annuler",
              html.Div(id=f"{px}-n", className="note"),
              dash_table.DataTable(
                  id=f"{px}-table", columns=colonnes, data=[], editable=True, row_selectable="multi",
                  sort_action="native", page_action="native", page_size=20,
                  dropdown={"execute_txt": {"options": [{"label": "Oui", "value": "Oui"}, {"label": "Non", "value": "Non"}],
                                            "clearable": False}},
                  style_data_conditional=[
                      {"if": {"filter_query": "{impact} > 0", "column_id": "impact"}, "color": "var(--vert)", "fontWeight": 600},
                      {"if": {"filter_query": "{impact} < 0", "column_id": "impact"}, "color": "var(--rouge)", "fontWeight": 600},
                      {"if": {"filter_query": '{statut} = "Exécutée"', "column_id": "statut"}, "color": "var(--vert)"},
                      {"if": {"filter_query": '{statut} = "Programmée"', "column_id": "statut"}, "color": "var(--navy)"},
                      {"if": {"filter_query": '{statut} = "Annulée"'}, "textDecoration": "line-through", "opacity": 0.55},
                      {"if": {"column_editable": True}, "backgroundColor": "var(--editable)"},
                  ],
                  **STYLE_TABLE, **style_texte(colonnes)),
              html.Div(className="ligne-boutons", children=[
                  html.Button("Annuler les opérations cochées", id=f"{px}-annuler", className="btn btn-secondaire")]),
              outils=segment(f"{px}-filtre", filtres, "jour")),
        *extra,
        carte("Fonctionnement", None, note(
            "Chaque opération est enregistrée dans tresorerie.db avec sa date de saisie ; rien n'est effacé : une "
            "opération annulée reste dans l'historique et chaque action (création, exécution, modification, annulation) "
            "est journalisée.",
            "Une opération impacte la journée de sa date de valeur : une date future en fait une opération programmée, "
            "affichée et comptée le jour J. Position exécutée = base + opérations exécutées ; position prévisionnelle = "
            "base + toutes les opérations du jour.",
            *(["Ferme : tant que le nombre de bons et le prix ne sont pas renseignés, le montant estimé est retenu ; une "
               "fois l'opération exécutée avec ces deux valeurs, le montant réel nombre de bons × prix remplace l'estimation."]
              if categorie == "ferme" else []),
            *(["Contreparties : si une échéance est saisie, le dénouement (capital + intérêts au taux, base 360) est "
               "programmé à cette date en sens inverse."] if categorie == "contrepartie" else []))),
    ])

    # --- Callbacks ------------------------------------------------------------------------

    if categorie == "contrepartie":
        @callback(Output(f"{px}-cp", "options"), Output(f"{px}-cp", "value"), Input(f"{px}-typecp", "value"))
        def options_cp(t):
            return TR.TYPES_CONTREPARTIE.get(t or "SDG", TR.SDG), None

    if categorie in ("rl", "ferme"):
        @callback(Output(f"{px}-codes", "children"), Input(jour_id, "date"))
        def liste_codes(_):
            return [html.Option(value=o["value"], label=o["label"]) for o in BT.options_codes()]

    etats = [State(f"{px}-type", "value"), State(f"{px}-date", "date"), State(f"{px}-montant", "value"),
             State(f"{px}-commentaire", "value"), State(f"{px}-execute", "value"), State(jour_id, "date")]
    if categorie == "contrepartie":
        etats += [State(f"{px}-typecp", "value"), State(f"{px}-cp", "value"), State(f"{px}-taux", "value"),
                  State(f"{px}-echeance", "date")]
    elif categorie == "rl":
        etats += [State(f"{px}-titre", "value"), State(f"{px}-libelle", "value"), State(f"{px}-cp", "value")]
    elif categorie == "sousrachat":
        etats += [State(f"{px}-fonds", "value"), State(f"{px}-libelle", "value")]
    else:
        etats += [State(f"{px}-titre", "value"), State(f"{px}-cp", "value"), State(f"{px}-nb", "value"),
                  State(f"{px}-prix", "value")]

    @callback(Output({"type": "sig-treso", "id": f"{px}-treso-3"}, "data"), Output(msg_id, "children", allow_duplicate=True),
              Output({"type": "sig-toast", "id": f"{px}-toast-0"}, "data"),
              Input(f"{px}-ajouter", "n_clicks"), State("treso-maj", "data"), *etats, prevent_initial_call=True,
              running=[(Output(f"{px}-ajouter", "disabled"), True, False)])
    def ajouter(n, maj, type_op, d, montant, commentaire, execute, jour, *spec):
        if not n:
            raise PreventUpdate
        if not d or montant in (None, "") or float(montant) <= 0:
            return no_update, no_update, toast("Renseignez la date de valeur et un montant positif.", "erreur")
        champs = {"commentaire": commentaire, "execute": bool(execute)}
        if categorie == "contrepartie":
            typecp, cp, taux, ech = spec
            if not cp:
                return no_update, no_update, toast("Choisissez la contrepartie.", "erreur")
            if ech and ech <= d:
                return no_update, no_update, toast("L'échéance doit être postérieure à la date de valeur.", "erreur")
            champs.update(type_contrepartie=typecp, contrepartie=cp, taux=taux, echeance=ech)
        elif categorie == "rl":
            titre, libelle, cp = spec
            f = BT.fiche(titre) if titre else None
            champs.update(titre=(titre or "").strip() or None, libelle=libelle or (f["description"] if f else None), contrepartie=cp)
        elif categorie == "sousrachat":
            fonds, libelle = spec
            if not fonds:
                return no_update, no_update, toast("Choisissez le fonds.", "erreur")
            champs.update(fonds=fonds, libelle=libelle)
        else:
            titre, cp, nb, prix = spec
            if execute and not (nb and prix):
                return no_update, no_update, toast("Exécution : renseignez le nombre de bons et le prix.", "erreur")
            f = BT.fiche(titre) if titre else None
            champs.update(titre=(titre or "").strip() or None, libelle=f["description"] if f else None, contrepartie=cp,
                          nb_bons=nb, prix=prix)
        avant = TR.position(d)["previsionnelle"]
        try:
            oid = TR.ajouter(type_op, d, float(montant), **champs)
        except Exception as e:                                               # noqa: BLE001
            return no_update, no_update, toast(f"Enregistrement impossible : {e}", "erreur")
        msg = f"Opération n° {oid} enregistrée. " + message_impact(d, avant)
        return time.time(), msg, toast(msg)

    @callback(Output(f"{px}-table", "data"), Output(f"{px}-table", "selected_rows"),
              Output(f"{px}-n", "children"),
              Input(jour_id, "date"), Input(f"{px}-filtre", "value"), Input("treso-maj", "data"))
    def rafraichir(jour, filtre, _):
        if not jour:
            raise PreventUpdate
        j = TR._iso(jour)
        if filtre == "programmees":
            df = TR.lire_operations(pd.Timestamp(j) + pd.Timedelta(days=1), None, categorie)
            lib = "programmées après la journée affichée"
        elif filtre == "historique":
            df = TR.lire_operations(None, None, categorie)
            df = df.sort_values(["date_valeur", "saisi_le"], ascending=False)
            lib = "dans l'historique (hors annulées)"
        elif filtre == "annulees":
            df = TR.lire_operations(None, None, categorie, annulees=True)
            df = df[df["annule"] == 1] if not df.empty else df
            lib = "annulées"
        else:
            df = TR.lire_operations(j, j, categorie)
            lib = f"du {fdate(j)}"
        imp = float(TR.position(j)["par_categorie"].get(categorie, 0.0))
        return lignes_table(df, j), [], (f"{len(df)} opération(s) {lib} · impact {cfg['titre'].lower()} du {fdate(j)} : "
                                         f"{mad(imp, True)}")

    @callback(Output({"type": "sig-treso", "id": f"{px}-treso-4"}, "data"), Output(msg_id, "children", allow_duplicate=True),
              Output({"type": "sig-toast", "id": f"{px}-toast-1"}, "data"),
              Input(f"{px}-table", "data_timestamp"), State(f"{px}-table", "data"), State(f"{px}-table", "data_previous"),
              State("treso-maj", "data"), State(jour_id, "date"), prevent_initial_call=True)
    def editer(_, data, precedent, maj, jour):
        if not data or not precedent:
            raise PreventUpdate
        avant_par_id = {r["id"]: r for r in precedent}
        changes = []
        for r in data:
            a = avant_par_id.get(r["id"])
            if not a or r.get("statut") == "Annulée":
                continue
            diff = {}
            for col, champ_db in (("execute_txt", "execute"), ("nb_bons", "nb_bons"), ("prix", "prix"),
                                  ("montant", "montant"), ("commentaire", "commentaire"), ("taux", "taux")):
                if col in r and r.get(col) != a.get(col):
                    diff[champ_db] = r.get(col)
            if diff:
                changes.append((r["id"], r["date_valeur"], diff, r))
        if not changes:
            raise PreventUpdate
        msgs = []
        for oid, dv, diff, r in changes:
            if "montant" in diff and (diff["montant"] in (None, "") or float(diff["montant"]) <= 0):
                return no_update, no_update, toast("Le montant doit rester strictement positif.", "erreur")
            if categorie == "ferme" and diff.get("execute") == "Oui" and not (r.get("nb_bons") and r.get("prix")):
                msgs.append(f"n° {oid} exécutée sans nombre de bons ni prix : le montant estimé reste retenu.")
            avant = TR.position(dv)["previsionnelle"]
            TR.modifier(oid, **diff)
            msgs.append(f"N° {oid} modifiée. " + message_impact(dv, avant))
        return time.time(), " ".join(msgs), toast(msgs[-1])

    @callback(Output({"type": "sig-treso", "id": f"{px}-treso-5"}, "data"), Output({"type": "sig-toast", "id": f"{px}-toast-2"}, "data"),
              Input(f"{px}-annuler", "n_clicks"), State(f"{px}-table", "selected_rows"), State(f"{px}-table", "data"),
              State("treso-maj", "data"), prevent_initial_call=True)
    def annuler(n, sel, data, maj):
        if not n:
            raise PreventUpdate
        if not sel:
            return no_update, toast("Cochez les opérations à annuler.", "erreur")
        ids = [data[i]["id"] for i in sel if data[i].get("statut") != "Annulée"]
        k = TR.annuler(ids)
        return time.time(), toast(f"{k} opération(s) annulée(s) (conservées dans l'historique).")

    if categorie == "contrepartie":
        @callback(Output(f"{px}-encours", "children"), Output(f"{px}-volumes", "children"),
                  Input(jour_id, "date"), Input("treso-maj", "data"))
        def contreparties(jour, _):
            from composants import table
            if not jour:
                raise PreventUpdate
            enc = TR.encours_contreparties(jour)
            vol = TR.volumes_contreparties(f"{pd.Timestamp(jour).year}-01-01", jour)
            t1 = table(enc, [{"col": "Type", "lib": "Type", "genre": "txt", "cls": "ref"},
                             {"col": "Emprunté", "lib": "Emprunté", "genre": "mad", "cls": ""},
                             {"col": "Placé", "lib": "Placé", "genre": "mad", "cls": ""},
                             {"col": "Net", "lib": "Net", "genre": "mad+", "cls": "j"},
                             {"col": "Opérations", "lib": "Opérations", "genre": "int", "cls": ""}],
                       premiere="Contrepartie", dense="dense") if not enc.empty else html.Div("Aucun encours.", className="note")
            if not vol.empty:
                vol = vol.assign(**{"Dernière opération": pd.to_datetime(vol["Dernière opération"]).dt.strftime("%d/%m/%Y")})
            t2 = table(vol, [{"col": "Type", "lib": "Type", "genre": "txt", "cls": "ref"},
                             {"col": "Emprunts", "lib": "Emprunts", "genre": "mad", "cls": ""},
                             {"col": "Placements", "lib": "Placements", "genre": "mad", "cls": ""},
                             {"col": "Opérations", "lib": "Opérations", "genre": "int", "cls": "j"},
                             {"col": "Dernière opération", "lib": "Dernière", "genre": "txt", "cls": "ref"}],
                       premiere="Contrepartie", dense="dense") if not vol.empty else html.Div("Aucune opération.", className="note")
            return t1, t2

    @callback(Output(f"{px}-dl", "data"), Input(f"{px}-export", "n_clicks"), prevent_initial_call=True)
    def exporter(n):
        if not n:
            raise PreventUpdate
        import base_courbe as db
        df = TR.lire_operations(None, None, categorie, annulees=True)
        return dcc.send_bytes(db.exporter_classeur({cfg["titre"][:30]: df}), f"tresorerie_{categorie}_{date.today():%Y%m%d}.xlsx")

    layout.children.append(signaux([f"{px}-toast-0", f"{px}-toast-1", f"{px}-toast-2", f"{px}-treso-3", f"{px}-treso-4", f"{px}-treso-5"]))
    return layout
