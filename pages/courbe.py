"""Fenêtre 1 — Courbe des taux : téléchargement BKAM, variations J-1 / hebdo / YTD."""

from __future__ import annotations

import time

from datetime import date, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash.exceptions import PreventUpdate
from dash import Input, Output, State, callback, dcc, html, no_update

import base_courbe as db
import theme as T
from composants import etiquette, fdate, jour, toast, signaux

try:                                    # le script s'appelle COURBE.py ou Courbe.py
    import COURBE as courbe
except ImportError:
    import Courbe as courbe

dash.register_page(__name__, path="/", name="Courbe des taux", order=0, section="courbe")

TENORS = db.TENORS
REFS = [("J-1", "Veille"), ("Hebdo", "Hebdomadaire"), ("YTD", "Year-to-date")]


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

layout = html.Div(className="page", children=[
    html.Div(className="entete", children=[
        html.Div("Courbe des taux", className="titre-fin"),
        html.Div("Marché secondaire des BDT", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),

    # Barre de contrôle : date, téléchargement, dates de référence compactes
    html.Div(className="carte barre", children=[
        html.Div(className="champ", children=[
            html.Label("Date de valorisation", htmlFor="date-valo"),
            dcc.Input(id="date-valo", type="date", value=date.today().isoformat(),
                      className="input-date"),
        ]),
        html.Button("Télécharger la courbe BKAM", id="btn-dl", className="btn btn-primaire"),
        html.Div(className="sep-v"),
        html.Div(id="puces", className="puces"),
    ]),
    html.Div(id="statut", className="statut"),

    html.Div(id="vide", className="vide"),

    html.Div(id="resultats", children=[
        html.Div(className="carte", children=[
            html.Div(className="carte-tete", children=[
                etiquette("Variations", "taux en %, variations en points de base"),
            ]),
            html.Div(id="tableau", className="tableau-defile"),
        ]),
        html.Div(className="carte", children=[
            html.Div(className="carte-tete", children=[
                etiquette("Courbe des taux", "J et courbe de comparaison"),
                dcc.RadioItems(
                    id="choix", value="J-1", inline=True, className="segment",
                    options=[{"label": k, "value": k} for k, _ in REFS],
                ),
            ]),
            dcc.Graph(id="graphe", config={"displayModeBar": False}, style={"height": "440px"}),
        ]),
        html.Details(className="carte details", children=[
            html.Summary(id="titre-brut"),
            html.Div(id="brut", className="tableau-defile"),
        ]),
    ]),
])
layout.children.append(signaux(['courbe-toast-0', 'courbe-maj-1']))


# ---------------------------------------------------------------------------
# Construction des éléments
# ---------------------------------------------------------------------------

def comparer(taux_j: dict, taux_ref: dict | None) -> dict:
    """{ténor: (taux ref, variation en pb)}"""
    res = {}
    for t in TENORS:
        r = taux_ref.get(t) if taux_ref else None
        res[t] = (r, (taux_j[t] - r) * 100 if r is not None else None)
    return res


def _num(v) -> str:
    return "—" if v is None else f"{v:.3f}%"


def _var(v) -> html.Td:
    if v is None:
        return html.Td("—", className="var stable")
    cls = "hausse" if v > 0.05 else "baisse" if v < -0.05 else "stable"
    return html.Td(f"{v:+.1f}", className=f"var {cls}")


def tableau_variations(valo, taux_j: dict, refs: dict, comps: dict) -> html.Table:
    tete1 = [html.Th("Ténor", rowSpan=2, className="gauche"),
             html.Th("Échéance", rowSpan=2),
             html.Th(["Taux J", html.Div(fdate(valo), className="th-date")], rowSpan=2,
                     className="col-j")]
    tete2 = []
    for k, lib in REFS:
        tete1.append(html.Th([lib, html.Div(fdate(refs[k]), className="th-date")],
                             colSpan=2, className="groupe"))
        tete2 += [html.Th("Taux", className="debut-groupe"), html.Th("Var. pb")]

    lignes = []
    for t, jours in TENORS.items():
        cellules = [html.Td(t, className="tenor gauche"),
                    html.Td(fdate(pd.Timestamp(valo) + timedelta(days=jours)), className="ech"),
                    html.Td(_num(taux_j[t]), className="j col-j")]
        for k, _ in REFS:
            r, v = comps[k][t]
            cellules += [html.Td(_num(r), className="ref debut-groupe"), _var(v)]
        lignes.append(html.Tr(cellules))

    return html.Table(className="tbl", children=[
        html.Thead([html.Tr(tete1), html.Tr(tete2)]), html.Tbody(lignes)])


def puces_dates(valo, refs: dict) -> list:
    def puce(lib, d, principal=False):
        return html.Div(className="puce principale" if principal else "puce", children=[
            html.Div(lib, className="puce-lib"),
            html.Div(fdate(d), className="puce-val"),
            html.Div(jour(d) or "non disponible", className="puce-sous"),
        ])
    return [puce("Valorisation", valo, True), puce("J-1", refs["J-1"]),
            puce("Hebdo", refs["Hebdo"]), puce("YTD", refs["YTD"])]


def graphique(taux_j: dict, taux_ref: dict | None, comp: dict, lib_j: str, lib_ref: str):
    x = list(TENORS)
    var = [comp[t][1] for t in x]
    fig = go.Figure()
    fig.add_bar(x=x, y=[v if v is not None else 0 for v in var], yaxis="y2", showlegend=False,
                marker=dict(color=[T.ROUGE if (v or 0) > 0 else T.VERT for v in var], opacity=0.18),
                hovertemplate="%{y:+.1f} pb<extra>Variation</extra>")
    if taux_ref:
        fig.add_scatter(x=x, y=[taux_ref[t] for t in x], name=lib_ref, mode="lines+markers",
                        line=dict(color=T.CIEL, width=2.2, dash="dot"), marker=dict(size=7),
                        hovertemplate="%{y:.3f}%<extra>" + lib_ref + "</extra>")
    fig.add_scatter(x=x, y=[taux_j[t] for t in x], name=lib_j, mode="lines+markers",
                    line=dict(color=T.NAVY, width=3),
                    marker=dict(size=8, color=T.NAVY, line=dict(color="#FFFFFF", width=1.5)),
                    hovertemplate="%{y:.3f}%<extra>" + lib_j + "</extra>")

    vmax = max([abs(v) for v in var if v is not None] or [0])
    pas = 5 * -(-max(5.0, vmax * 3.2) // 15)           # graduation ronde (multiple de 5)
    fig.update_layout(
        **T.PLOTLY_LAYOUT, bargap=0.55,
        xaxis=dict(showgrid=False, linecolor="rgba(11,74,139,0.18)", tickfont=dict(color=T.GRIS)),
        yaxis=dict(title=dict(text="Taux (%)", font=dict(color=T.GRIS, size=11)),
                   gridcolor="rgba(11,74,139,0.07)", zeroline=False, ticksuffix="%",
                   tickfont=dict(color=T.GRIS)),
        yaxis2=dict(title=dict(text="Variation (pb)", font=dict(color=T.GRIS_CLAIR, size=11)),
                    overlaying="y", side="right", showgrid=False, range=[-3 * pas, 3 * pas],
                    tickmode="linear", tick0=0, dtick=pas, tickformat="+.0f",
                    zeroline=True, zerolinecolor="rgba(11,74,139,0.12)",
                    tickfont=dict(color=T.GRIS_CLAIR)),
    )
    return fig


def tableau_brut(points: pd.DataFrame) -> html.Table | html.Div:
    if points.empty:
        return html.Div("Date issue de l'historique importé : points de marché non disponibles. "
                        "Retéléchargez la courbe pour les obtenir.", className="note")
    tete = html.Tr([html.Th("Échéance", className="gauche"), html.Th("Maturité (j)"),
                    html.Th("Taux"), html.Th("Transaction"), html.Th("Date de valeur")])
    lignes = [html.Tr([
        html.Td(fdate(r.date_echeance), className="gauche"),
        html.Td(f"{r.maturite_residuelle:,.0f}".replace(",", " ")),
        html.Td(_num(r.taux), className="j"),
        html.Td("—" if pd.isna(r.transaction) else f"{r.transaction:,.0f}".replace(",", " ")),
        html.Td(fdate(r.date_valeur), className="ech"),
    ]) for r in points.itertuples()]
    return html.Table(className="tbl compact", children=[html.Thead(tete), html.Tbody(lignes)])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "sig-maj", "id": "courbe-maj-1"}, "data"),
    Output({"type": "sig-toast", "id": "courbe-toast-0"}, "data"),
    Input("btn-dl", "n_clicks"),
    State("date-valo", "value"), State("maj", "data"),
    running=[(Output("btn-dl", "disabled"), True, False),
             (Output("btn-dl", "children"), "Téléchargement…", "Télécharger la courbe BKAM")],
    prevent_initial_call=True,
)
def telecharger(n_clicks, valo, maj):
    if not n_clicks:                     # pas de clic réel (insertion de la page)
        raise PreventUpdate
    if not valo:
        return no_update, toast("Choisissez une date de valorisation.", "erreur")
    valo = date.fromisoformat(valo)
    try:
        brute = courbe.get_bkam_taux_reference(valo)
    except Exception as e:
        return no_update, toast(f"Échec du téléchargement BKAM : {e}", "erreur")
    if len(brute) < 2:
        return no_update, toast(f"BKAM n'a pas publié de courbe pour le {valo:%d/%m/%Y} "
                                "(week-end, jour férié ou pas encore disponible).", "erreur")

    tab = courbe.tableau_tenors(brute, valo, formate=False)
    taux = dict(zip(tab["Ténor"], tab.iloc[:, 2]))
    with db.connexion() as conn:
        db.enregistrer_courbe(conn, valo, taux, brute, source="bkam")
    return maj + 1, toast(f"Courbe du {valo:%d/%m/%Y} téléchargée et historisée.")


@callback(
    Output("puces", "children"), Output("statut", "children"),
    Output("vide", "children"), Output("vide", "style"), Output("resultats", "style"),
    Output("tableau", "children"), Output("graphe", "figure"),
    Output("titre-brut", "children"), Output("brut", "children"),
    Input("date-valo", "value"), Input("maj", "data"), Input("choix", "value"), Input("ui", "data"),
)
def afficher(valo, _, choix, ui):
    cache = {"display": "none"}
    if not valo:
        return [], "", "Choisissez une date de valorisation.", {}, cache, *[no_update] * 4
    valo = date.fromisoformat(valo)

    with db.connexion() as conn:
        taux_j = db.lire_tenors(conn, valo)
        refs = {"J-1": db.date_veille(conn, valo), "Hebdo": db.date_hebdo(conn, valo),
                "YTD": db.date_ytd(conn, valo)}
        taux_refs = {k: db.lire_tenors(conn, d) if d is not None else None for k, d in refs.items()}
        points = db.lire_courbe_bkam(conn, valo)

    puces = puces_dates(valo, refs)
    if taux_j is None:
        weekend = " C'est un week-end." if valo.weekday() >= 5 else ""
        vide = [html.Div(f"Aucune courbe en base pour le {valo:%d/%m/%Y}.{weekend}",
                         className="vide-titre"),
                html.Div("Téléchargez la courbe BKAM de cette date, ou choisissez une date "
                         "déjà historisée.", className="vide-texte")]
        return puces, "", vide, {}, cache, *[no_update] * 4

    source = "BKAM" if taux_j["source"] == "bkam" else "historique importé"
    statut = f"Courbe en base · source {source} · enregistrée le " \
             f"{pd.Timestamp(taux_j['maj']):%d/%m/%Y à %H:%M}"

    comps = {k: comparer(taux_j, taux_refs[k]) for k, _ in REFS}
    choix = choix or "J-1"
    fig = graphique(taux_j, taux_refs[choix], comps[choix],
                    f"J · {valo:%d/%m/%Y}", f"{choix} · {fdate(refs[choix])}")

    return (puces, statut, [], cache, {},
            tableau_variations(valo, taux_j, refs, comps), T.theme_fig(fig, ui),
            f"Points de marché BKAM du {valo:%d/%m/%Y}", tableau_brut(points))
