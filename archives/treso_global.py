"""
ARCHIVE — page retirée de l'application, non chargée (voir pages/treso_global.py).

Trésorerie — tableau de bord de la journée : position (toujours visible en haut), puis onglets
Vue d'ensemble (base, ajustements, impact de chaque opération, flux à venir, journal), Contreparties,
Règlements / livraisons, Souscriptions / rachats et Ferme.
"""

from __future__ import annotations

import time

from datetime import date

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate

import base_courbe as db
import tresorerie as TR
import theme as T
import vue_tresorerie as V
from composants import carte, etiquette, fdate, graphe, mise_en_page, note, table, toast, signaux

dash.register_page(__name__, path="/tresorerie", name="Tableau de bord", order=0, section="tresorerie")

COL_IMPACT = [
    {"id": "saisi_txt", "name": "Saisie le"}, {"id": "categorie_lib", "name": "Catégorie"},
    {"id": "type_op", "name": "Opération"}, {"id": "detail", "name": "Détail"},
    {"id": "montant_retenu", "name": "Montant", "type": "numeric", "format": V.F2},
    {"id": "impact", "name": "Impact", "type": "numeric", "format": V.F2S},
    {"id": "execute_txt", "name": "Exécutée"},
    {"id": "position_previsionnelle", "name": "Position prévisionnelle après", "type": "numeric", "format": V.F2S},
    {"id": "position_executee", "name": "Position exécutée après", "type": "numeric", "format": V.F2S},
]
COL_VENIR = [
    {"id": "date_txt", "name": "Date de valeur"}, {"id": "categorie_lib", "name": "Catégorie"},
    {"id": "type_op", "name": "Opération"}, {"id": "detail", "name": "Détail"},
    {"id": "montant_retenu", "name": "Montant", "type": "numeric", "format": V.F2},
    {"id": "impact", "name": "Impact", "type": "numeric", "format": V.F2S},
    {"id": "execute_txt", "name": "Exécutée"},
]
COL_JOURNAL = [{"id": "horodatage_txt", "name": "Horodatage"}, {"id": "operation_id", "name": "N°"},
               {"id": "action", "name": "Action"}, {"id": "detail", "name": "Détail"}]
COND = [
    {"if": {"filter_query": "{impact} > 0", "column_id": "impact"}, "color": "var(--vert)", "fontWeight": 600},
    {"if": {"filter_query": "{impact} < 0", "column_id": "impact"}, "color": "var(--rouge)", "fontWeight": 600},
    {"if": {"column_id": "position_previsionnelle"}, "fontWeight": 700},
]


def _table(id_, cols, page=20):
    return dash_table.DataTable(id=id_, columns=cols, data=[], sort_action="native", page_action="native",
                                page_size=page, style_data_conditional=COND, **V.STYLE_TABLE, **V.style_texte(cols))


def _detail(r) -> str:
    morceaux = [r.get("contrepartie"), r.get("fonds"), r.get("titre"), r.get("libelle"), r.get("commentaire")]
    return " · ".join(str(m) for m in morceaux if m not in (None, "") and not (isinstance(m, float) and np.isnan(m)))


ONGLETS = [("contrepartie", "Contreparties"), ("rl", "Règlements / livraisons"),
           ("sousrachat", "Souscriptions / rachats"), ("ferme", "Ferme")]

layout = html.Div(className="page", children=[
    dcc.Download(id="tg-dl"),
    html.Div(className="entete", children=[
        html.Div("Trésorerie", className="titre-fin"),
        html.Div("Gestion de la trésorerie de la journée", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    # Bandeau toujours visible : journée, dernière opération, position
    html.Div(className="bandeau-treso", children=[
        html.Div(className="carte barre", children=[
            V.champ("Journée", V.jour_fr("tg-jour", date.today().isoformat())),
            html.Div(id="tg-msg", className="note impact-msg"),
            html.Button("Exporter la journée", id="tg-export", className="btn btn-secondaire"),
        ]),
        html.Div(className="carte", children=[html.Div(id="tg-kpi", className="kpi-grille kpi-6")]),
    ]),
    dcc.Tabs(id="tg-onglets", value="ensemble", className="onglets", children=[
        dcc.Tab(label="Vue d'ensemble", value="ensemble", className="onglet", selected_className="onglet-actif",
                children=html.Div(className="onglet-corps", children=[
        html.Div(className="grille-2", children=[
            html.Div(className="carte", children=[
                html.Div(className="carte-tete", children=[etiquette("Position de base", "reprise du système d'information")]),
                html.Div(className="barre barre-interne tresorerie-saisie", children=[
                    V.champ("Montant (+ à placer, − à emprunter)", V.nombre("tg-base", placeholder="ex. -25000000")),
                    V.champ("Commentaire", V.texte("tg-base-com", placeholder="facultatif"), "champ-large"),
                ]),
                html.Div(className="ligne-boutons", children=[
                    html.Button("Enregistrer la position", id="tg-base-ok", className="btn btn-primaire"),
                    html.Button("Reprendre la clôture du jour précédent", id="tg-reprise", className="btn btn-lien"),
                ]),
                html.Div(id="tg-base-histo", className="tableau-defile", style={"marginTop": "0.8rem"}),
            ]),
            html.Div(className="carte", children=[
                html.Div(className="carte-tete", children=[etiquette("Ajustement", "écart avec le système d'information")]),
                html.Div(className="barre barre-interne tresorerie-saisie", children=[
                    V.champ("Montant (±)", V.nombre("tg-ajust", placeholder="ex. -125000")),
                    V.champ("Motif", V.texte("tg-ajust-com", placeholder="facultatif"), "champ-large"),
                ]),
                html.Div(className="ligne-boutons", children=[
                    html.Button("Ajouter l'ajustement", id="tg-ajust-ok", className="btn btn-primaire")]),
                html.Div("L'ajustement est historisé comme une opération (exécutée) ; la position de base, elle, remplace "
                         "la précédente saisie du jour.", className="note", style={"marginTop": "0.8rem"}),
            ]),
        ]),
        html.Div(className="grille-2 grille-backtest", children=[
            carte("Impact par catégorie", "sur la journée", html.Div(id="tg-categories", className="tableau-defile")),
            carte("De la base à la position prévisionnelle", None, graphe("tg-g-cascade", 320)),
        ]),
        carte("Impact de chaque opération", "dans l'ordre de saisie, avec la position après chacune",
              graphe("tg-g-chrono", 260), _table("tg-chrono", COL_IMPACT)),
        carte("À venir", "opérations programmées et dénouements des 30 prochains jours",
              html.Div(id="tg-venir-n", className="note"), _table("tg-venir", COL_VENIR, 15)),
        html.Div(className="grille-2", children=[
            carte("Historique des positions de base", "toutes journées", html.Div(id="tg-base-tout", className="tableau-defile")),
            carte("Journal", "dernières actions", _table("tg-journal", COL_JOURNAL, 12)),
        ]),
        carte("Fonctionnement", None, note(
            "Position de base : montant global à placer (+) ou à emprunter (−) repris du système d'information pour la "
            "journée ; vous pouvez la ressaisir à tout moment (la dernière saisie fait foi, les précédentes restent "
            "historisées) ou reprendre la position prévisionnelle de clôture du dernier jour saisi.",
            "Toutes les opérations des fenêtres Contreparties, Règlements / livraisons, Souscriptions / rachats et Ferme "
            "dont la date de valeur est la journée affichée s'ajoutent à cette base, ainsi que les dénouements des "
            "emprunts / placements arrivant à échéance. Position exécutée : opérations cochées « exécutée » ; position "
            "prévisionnelle : toutes les opérations.")),
                ])),
        *[dcc.Tab(label=lib, value=cat, className="onglet", selected_className="onglet-actif",
                  children=html.Div(className="onglet-corps", children=[V.construire(cat, "tg-jour", "tg-msg")]))
          for cat, lib in ONGLETS],
    ]),
])
layout.children.append(signaux(['treso_global-toast-0', 'treso_global-toast-1', 'treso_global-treso-2', 'treso_global-treso-3']))


@callback(Output({"type": "sig-treso", "id": "treso_global-treso-2"}, "data"), Output("tg-msg", "children", allow_duplicate=True),
          Output("tg-base", "value"), Output({"type": "sig-toast", "id": "treso_global-toast-0"}, "data"),
          Input("tg-base-ok", "n_clicks"), Input("tg-reprise", "n_clicks"),
          State("tg-base", "value"), State("tg-base-com", "value"), State("tg-jour", "date"), State("treso-maj", "data"),
          prevent_initial_call=True, running=[(Output("tg-base-ok", "disabled"), True, False)])
def base(n_ok, n_rep, montant, com, jour, maj):
    from dash import ctx
    if not (n_ok or n_rep) or not jour:
        raise PreventUpdate
    avant = TR.position(jour)["previsionnelle"]
    if ctx.triggered_id == "tg-reprise":
        c = TR.cloture_precedente(jour)
        if not c:
            return no_update, no_update, no_update, toast("Aucune journée antérieure avec une position de base.", "erreur")
        TR.saisir_base(jour, c[1], f"reprise de la clôture du {fdate(c[0])}")
    else:
        if montant in (None, ""):
            return no_update, no_update, no_update, toast("Saisissez le montant de la position de base.", "erreur")
        TR.saisir_base(jour, float(montant), com)
    msg = "Position de base enregistrée. " + V.message_impact(jour, avant)
    return time.time(), msg, None, toast(msg)


@callback(Output({"type": "sig-treso", "id": "treso_global-treso-3"}, "data"), Output("tg-msg", "children", allow_duplicate=True),
          Output("tg-ajust", "value"), Output({"type": "sig-toast", "id": "treso_global-toast-1"}, "data"),
          Input("tg-ajust-ok", "n_clicks"), State("tg-ajust", "value"), State("tg-ajust-com", "value"),
          State("tg-jour", "date"), State("treso-maj", "data"), prevent_initial_call=True,
          running=[(Output("tg-ajust-ok", "disabled"), True, False)])
def ajustement(n, montant, com, jour, maj):
    if not n or not jour:
        raise PreventUpdate
    if montant in (None, "") or float(montant) == 0:
        return no_update, no_update, no_update, toast("Saisissez un ajustement non nul (signé).", "erreur")
    avant = TR.position(jour)["previsionnelle"]
    oid = TR.ajouter("Ajustement", jour, float(montant), commentaire=com)
    msg = f"Ajustement n° {oid} enregistré. " + V.message_impact(jour, avant)
    return time.time(), msg, None, toast(msg)


@callback(Output("tg-kpi", "children"), Output("tg-categories", "children"), Output("tg-g-cascade", "figure"),
          Output("tg-g-chrono", "figure"), Output("tg-chrono", "data"), Output("tg-venir", "data"),
          Output("tg-venir-n", "children"), Output("tg-base-histo", "children"), Output("tg-base-tout", "children"),
          Output("tg-journal", "data"),
          Input("tg-jour", "date"), Input("treso-maj", "data"), Input("ui", "data"))
def afficher(jour, _, ui):
    if not jour:
        raise PreventUpdate
    p = TR.position(jour)

    # Impact par catégorie
    cats = [c for c in TR.CATEGORIES if c in p["par_categorie"].index]
    ch = TR.impact_chronologique(jour)
    lignes = []
    for c in cats:
        sous = ch[ch["categorie"] == c]
        lignes.append({"Catégorie": TR.CATEGORIES[c], "Opérations": len(sous),
                       "Exécuté": float(sous.loc[sous["execute"], "impact"].sum()),
                       "En attente": float(sous.loc[~sous["execute"], "impact"].sum()),
                       "Total": float(sous["impact"].sum())})
    t = pd.DataFrame(lignes).set_index("Catégorie") if lignes else pd.DataFrame(columns=["Opérations", "Exécuté", "En attente", "Total"])
    t.loc["Position de base"] = [np.nan, p["base"], 0.0, p["base"]]
    t = t.reindex(["Position de base"] + [i for i in t.index if i != "Position de base"])
    t.loc["Position"] = [t["Opérations"].sum(), p["executee"], p["en_attente"], p["previsionnelle"]]
    t_cat = table(t, [{"col": "Opérations", "lib": "Opérations", "genre": "int", "cls": "ref"},
                      {"col": "Exécuté", "lib": "Exécuté", "genre": "mad+", "cls": ""},
                      {"col": "En attente", "lib": "En attente", "genre": "mad+", "cls": ""},
                      {"col": "Total", "lib": "Total", "genre": "mad+", "cls": "j"}],
                  premiere="Catégorie", dense="dense",
                  styles=lambda i, c, v: {"fontWeight": 700} if i in ("Position", "Position de base") else None)

    # Cascade
    noms = ["Base"] + [TR.CATEGORIES[c] for c in cats] + ["Prévisionnelle"]
    valeurs = [p["base"]] + [float(p["par_categorie"][c]) for c in cats] + [p["previsionnelle"]]
    g1 = go.Figure(go.Waterfall(x=noms, y=valeurs, measure=["absolute"] + ["relative"] * len(cats) + ["total"],
                                increasing=dict(marker=dict(color=T.VERT)), decreasing=dict(marker=dict(color=T.ROUGE)),
                                totals=dict(marker=dict(color=T.NAVY)), connector=dict(line=dict(color=T.GRIS_CLAIR, width=1)),
                                text=[V.mad(v, True) for v in valeurs], textposition="outside",
                                hovertemplate="%{x} : %{y:,.2f}<extra></extra>"))
    g1 = mise_en_page(g1, 320, yaxis_title="MAD", showlegend=False)
    g1.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)

    # Chronologie
    g2 = go.Figure()
    if not ch.empty:
        etapes = list(range(len(ch) + 1))
        g2.add_scatter(x=etapes, y=[p["base"]] + ch["position_previsionnelle"].tolist(), mode="lines+markers",
                       line_shape="hv", name="Prévisionnelle", line=dict(color=T.NAVY, width=2.5),
                       text=["Base"] + [f"n° {i} {o}" for i, o in zip(ch["id"], ch["type_op"])],
                       hovertemplate="%{text}<br>%{y:,.2f}<extra>Prévisionnelle</extra>")
        g2.add_scatter(x=etapes, y=[p["base"]] + ch["position_executee"].tolist(), mode="lines+markers", line_shape="hv",
                       name="Exécutée", line=dict(color=T.MENTHE, width=2, dash="dot"),
                       text=["Base"] + [f"n° {i} {o}" for i, o in zip(ch["id"], ch["type_op"])],
                       hovertemplate="%{text}<br>%{y:,.2f}<extra>Exécutée</extra>")
    g2 = mise_en_page(g2, 260, xaxis_title="Opérations successives", yaxis_title="MAD", legend=dict(orientation="h", y=1.15, x=0))
    g2.add_hline(y=0, line_color=T.GRIS_CLAIR, line_width=1)

    def prep(df):
        if df.empty:
            return []
        d = df.copy()
        d["detail"] = d.apply(_detail, axis=1)
        return V.lignes_table(d, jour)

    venir = TR.a_venir(jour)
    n_venir = (f"{len(venir)} flux · impact cumulé {V.mad(float(venir['impact'].sum()), True)}" if not venir.empty
               else "Aucune opération programmée.")

    hb = TR.historique_base(jour)
    hb_div = (table(hb.assign(saisi=pd.to_datetime(hb["saisi_le"]).dt.strftime("%d/%m %H:%M")).set_index("saisi")[["montant", "commentaire"]],
                    [{"col": "montant", "lib": "Montant", "genre": "mad+", "cls": "j"},
                     {"col": "commentaire", "lib": "Commentaire", "genre": "txt", "cls": "ref"}],
                    premiere="Saisies du jour", dense="dense") if not hb.empty
              else html.Div("Aucune position de base pour cette journée.", className="note"))
    ht = TR.historique_base()
    ht_div = (table(ht.assign(saisi=pd.to_datetime(ht["saisi_le"]).dt.strftime("%d/%m/%Y %H:%M"),
                              jour=pd.to_datetime(ht["date_jour"]).dt.strftime("%d/%m/%Y")).set_index("saisi")[["jour", "montant", "commentaire"]],
                    [{"col": "jour", "lib": "Journée", "genre": "txt", "cls": "ref"},
                     {"col": "montant", "lib": "Montant", "genre": "mad+", "cls": "j"},
                     {"col": "commentaire", "lib": "Commentaire", "genre": "txt", "cls": "ref"}],
                    premiere="Saisie le", dense="dense") if not ht.empty else html.Div("Aucune saisie.", className="note"))
    jn = TR.lire_journal(200)
    if not jn.empty:
        jn["horodatage_txt"] = pd.to_datetime(jn["horodatage"]).dt.strftime("%d/%m/%Y %H:%M:%S")
        jn["operation_id"] = jn["operation_id"].astype("Int64").astype(str).replace("<NA>", "")
    return (V.kpis_position(jour), t_cat, T.theme_fig(g1, ui), T.theme_fig(g2, ui), prep(ch), prep(venir), n_venir,
            hb_div, ht_div, jn.replace({np.nan: None}).to_dict("records"))


@callback(Output("tg-dl", "data"), Input("tg-export", "n_clicks"), State("tg-jour", "date"), prevent_initial_call=True)
def exporter(n, jour):
    if not n:
        raise PreventUpdate
    p = TR.position(jour)
    resume = pd.DataFrame([("Journée", fdate(jour)), ("Position de base", p["base"]), ("Position exécutée", p["executee"]),
                           ("En attente", p["en_attente"]), ("Position prévisionnelle", p["previsionnelle"]),
                           ("Besoin", TR.besoin(p["previsionnelle"]))], columns=["Indicateur", "Valeur"]).astype({"Valeur": str})
    feuilles = {"Position": resume, "Opérations du jour": TR.impact_chronologique(jour), "À venir": TR.a_venir(jour),
                "Toutes les opérations": TR.lire_operations(None, None, annulees=True),
                "Positions de base": TR.historique_base(n=100000), "Journal": TR.lire_journal(100000)}
    return dcc.send_bytes(db.exporter_classeur(feuilles), f"tresorerie_{(jour or '').replace('-', '')}.xlsx")
