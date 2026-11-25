"""Fenêtre 4 — Stress test : courbe BKAM modifiable, impact sur les ténors et résolution inverse."""

from __future__ import annotations

import time

from datetime import date, timedelta

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Scheme, Sign
from dash.exceptions import PreventUpdate

import base_courbe as db
import stress as S
import theme as T
from composants import carte, etiquette, fdate, graphe, mise_en_page, note, segment, toast, signaux

dash.register_page(__name__, path="/stress", name="Stress test", order=3, section="courbe")

TENORS = db.TENORS
F3 = Format(precision=3, scheme=Scheme.fixed)
F_PB = Format(precision=1, scheme=Scheme.fixed, sign=Sign.positive)

STYLE_TABLE = dict(
    style_as_list_view=True,
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600,
                  "fontSize": "0.62rem", "letterSpacing": "0.12em", "textTransform": "uppercase",
                  "border": "none", "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.6rem",
                  "textAlign": "right"},
    style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.86rem",
                "padding": "0.5rem 0.6rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
)


def colonnes_points():
    return [
        {"id": "echeance", "name": "Échéance", "editable": False},
        {"id": "maturite", "name": "Maturité (j)", "type": "numeric", "editable": True},
        {"id": "taux_orig", "name": "Taux d'origine", "type": "numeric", "format": F3, "editable": False},
        {"id": "taux", "name": "Taux stressé ✎", "type": "numeric", "format": F3, "editable": True},
        {"id": "ecart", "name": "Écart (pb)", "type": "numeric", "format": F_PB, "editable": False},
    ]


def colonnes_tenors():
    return [
        {"id": "tenor", "name": "Ténor", "editable": False},
        {"id": "echeance", "name": "Échéance", "editable": False},
        {"id": "orig", "name": "Origine", "type": "numeric", "format": F3, "editable": False},
        {"id": "stresse", "name": "Stressé ✎", "type": "numeric", "format": F3, "editable": True},
        {"id": "delta", "name": "Δ (pb)", "type": "numeric", "format": F_PB, "editable": False},
    ]


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

layout = html.Div(className="page", children=[
    dcc.Store(id="sx-orig"),               # ténors et points de la courbe d'origine
    dcc.Download(id="sx-dl"),
    html.Div(className="entete", children=[
        html.Div("Stress test", className="titre-fin"),
        html.Div("Courbe BKAM modifiable et impact sur les ténors", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),

    html.Div(className="carte barre", children=[
        html.Div(className="champ", children=[
            html.Label("Date de la courbe"),
            dcc.Input(id="sx-date", type="date", className="input-date"),
        ]),
        html.Button("Recharger l'origine", id="sx-reset", className="btn btn-secondaire bouton-gauche"),
        html.Button("Télécharger depuis BKAM", id="sx-bkam", className="btn btn-secondaire bouton-gauche"),
        html.Div(className="sep-v"),
        html.Div(className="champ", children=[
            html.Label("Choc court (pb)"),
            dcc.Input(id="sx-court", type="number", value=0, step=1, className="input-date input-nombre"),
        ]),
        html.Div(className="champ", children=[
            html.Label("Choc long (pb)"),
            dcc.Input(id="sx-long", type="number", value=0, step=1, className="input-date input-nombre"),
        ]),
        html.Button("Appliquer le choc", id="sx-choc", className="btn btn-primaire"),
        html.Button("Exporter", id="sx-export", className="btn btn-secondaire"),
    ]),
    html.Div(id="sx-source", className="statut"),
    html.Div(id="sx-message"),

    html.Div(className="grille-2 grille-large-gauche", children=[
        carte("Points de la courbe", "modifiez une maturité ou un taux ; les ténors suivent",
              dash_table.DataTable(id="sx-points", columns=colonnes_points(), data=[], row_deletable=True,
                                   **STYLE_TABLE,
                                   style_data_conditional=[
                                       {"if": {"column_id": ["maturite", "taux"]}, "backgroundColor": "var(--editable)"},
                                       {"if": {"filter_query": '{statut} = "modifie"', "column_id": ["taux", "ecart"]},
                                        "color": "var(--rouge)", "fontWeight": 600},
                                       {"if": {"filter_query": '{statut} = "nouveau"'}, "color": "var(--vert)",
                                        "fontWeight": 600},
                                       {"if": {"column_id": "echeance"}, "textAlign": "left", "color": "var(--gris)"},
                                   ]),
              html.Button("+ Ajouter un point", id="sx-ajout", className="btn btn-lien")),
        carte("Ténors", "modifiez un ténor : la courbe est ajustée pour le redonner",
              dash_table.DataTable(id="sx-tenors", columns=colonnes_tenors(), data=[], **STYLE_TABLE,
                                   style_data_conditional=[
                                       {"if": {"column_id": "stresse"}, "backgroundColor": "var(--editable)",
                                        "fontWeight": 600},
                                       {"if": {"column_id": "tenor"}, "textAlign": "left", "color": "var(--navy)",
                                        "fontWeight": 600},
                                       {"if": {"column_id": "echeance"}, "color": "var(--gris)"},
                                       {"if": {"filter_query": "{delta} > 0.05", "column_id": "delta"},
                                        "color": "var(--rouge)", "fontWeight": 600},
                                       {"if": {"filter_query": "{delta} < -0.05", "column_id": "delta"},
                                        "color": "var(--vert)", "fontWeight": 600},
                                   ]),
              outils=html.Div(className="champ", children=[
                  html.Label("Ajustement de la courbe"),
                  segment("sx-mode", [(v, k) for k, v in S.MODES.items()], "encadrants"),
              ])),
    ]),

    carte("Courbe d'origine et courbe stressée", "interpolation selon les règles de TauxCourbe",
          graphe("sx-g-courbe", 420)),
    html.Div(className="grille-2", children=[
        carte("Impact sur les ténors", "variation en pb", graphe("sx-g-delta", 320)),
        carte("Méthode", None, note(
            "Les ténors sont recalculés avec taux_courbe de COURBE.py : taux plat sous le premier point, "
            "interpolation linéaire entre deux points, conversion monétaire ↔ actuariel quand l'intervalle "
            "franchit un an, extrapolation à partir des deux derniers points. Le saut visible vers 365 jours "
            "sur la courbe est ce passage du taux monétaire au taux actuariel.",
            "Quand vous modifiez un ténor, la courbe est ajustée selon le mode choisi : « Points encadrants » "
            "déplace du même montant les deux points qui servent à son interpolation ; « Point le plus proche » "
            "ne déplace que le point de maturité la plus proche ; « Translation parallèle » déplace toute la "
            "courbe ; « Insérer un point » ajoute un point à la maturité du ténor. Le déplacement est calculé "
            "numériquement pour que le ténor retrouve exactement le taux saisi ; les autres ténors qui "
            "dépendent des mêmes points bougent aussi.",
            "Choc : déplacement linéaire en maturité entre le premier point (choc court) et le dernier "
            "(choc long). Parallèle si les deux sont égaux, pentification si le choc long est plus fort.")),
    ]),
])
layout.children.append(signaux(['stress-toast-0', 'stress-maj-1']))


# ---------------------------------------------------------------------------
# Chargement de la courbe
# ---------------------------------------------------------------------------

def charger(d: str) -> tuple[list[dict], str]:
    with db.connexion() as conn:
        brute = db.lire_courbe_bkam(conn, d)
        tenors = db.lire_tenors(conn, d)
    if len(brute) >= 2:
        return S.lignes_depuis_bkam(brute), f"Courbe BKAM du {fdate(d)} · {len(brute)} points de marché."
    if tenors:
        return S.lignes_depuis_tenors(tenors, d), (
            f"Pas de points de marché BKAM en base pour le {fdate(d)} : courbe reconstituée à partir des "
            "9 ténors historisés. Utilisez « Télécharger depuis BKAM » pour obtenir les points réels.")
    return [], f"Aucune courbe en base pour le {fdate(d)}."


def origine(lignes: list[dict], d: str) -> dict:
    df = S.vers_df([{**p, "taux": p["taux_orig"]} for p in lignes if p.get("taux_orig") is not None])
    if len(df) < 2:
        return {}
    return {"tenors": S.tableau_tenors(df, d),
            "points": df[["maturite_residuelle", "taux_moyen_pondere"]].to_dict("records")}


@callback(Output("sx-date", "value"), Input("maj", "data"), Input("sx-date", "id"), State("sx-date", "value"))
def date_defaut(_, _montage, actuelle):
    if actuelle:
        raise PreventUpdate
    with db.connexion() as conn:
        d = conn.execute("SELECT MAX(date_courbe) FROM courbe_bkam").fetchone()[0] \
            or conn.execute("SELECT MAX(date) FROM tenors").fetchone()[0]
    if not d:
        raise PreventUpdate
    return d


@callback(
    Output("sx-points", "data"), Output("sx-orig", "data"), Output("sx-source", "children"),
    Output("sx-message", "children"), Output({"type": "sig-maj", "id": "stress-maj-1"}, "data"),
    Input("sx-date", "value"), Input("sx-reset", "n_clicks"), Input("sx-bkam", "n_clicks"),
    Input("sx-choc", "n_clicks"), Input("sx-ajout", "n_clicks"),
    Input("sx-points", "data_timestamp"), Input("sx-tenors", "data_timestamp"),
    State("sx-points", "data"), State("sx-tenors", "data"), State("sx-mode", "value"),
    State("sx-court", "value"), State("sx-long", "value"), State("maj", "data"),
    prevent_initial_call=True,
)
def agir(d, _r, _b, _c, _a, _tp, _tt, points, tenors, mode, court, long_, maj):
    if not d:
        raise PreventUpdate
    declencheur = ctx.triggered_id
    points = points or []

    # Chargement de la courbe d'origine
    if declencheur in ("sx-date", "sx-reset"):
        lignes, source = charger(d)
        return S.normaliser(lignes, d), origine(lignes, d), source, None, no_update

    if declencheur == "sx-bkam":
        if not _b:
            raise PreventUpdate
        try:
            brute = S.courbe.get_bkam_taux_reference(pd.Timestamp(d).to_pydatetime())
        except Exception as e:
            return no_update, no_update, no_update, html.Div(f"Échec du téléchargement BKAM : {e}",
                                                             className="alerte"), no_update
        if len(brute) < 2:
            return no_update, no_update, no_update, html.Div(
                f"BKAM n'a pas publié de courbe pour le {fdate(d)}.", className="alerte"), no_update
        tab = S.courbe.tableau_tenors(brute, pd.Timestamp(d).to_pydatetime(), formate=False)
        with db.connexion() as conn:
            db.enregistrer_courbe(conn, d, dict(zip(tab["Ténor"], tab.iloc[:, 2])), brute, source="bkam")
        lignes, source = charger(d)
        return S.normaliser(lignes, d), origine(lignes, d), source, None, time.time()

    # Choc sur toute la courbe
    if declencheur == "sx-choc":
        c, l = float(court or 0), float(long_ or 0)
        if c == 0 and l == 0:
            raise PreventUpdate
        msg = (f"Choc parallèle de {c:+.0f} pb appliqué." if c == l
               else f"Choc de {c:+.0f} pb (court) à {l:+.0f} pb (long) appliqué.")
        return S.normaliser(S.choc_lineaire(points, c, l), d), no_update, no_update, \
            html.Div(msg, className="info"), no_update

    if declencheur == "sx-ajout":
        ref = next((p.get("valeur") for p in points if p.get("valeur")), d)
        points = points + [{"maturite": None, "taux": None, "taux_orig": None, "valeur": ref}]
        return S.normaliser(points, d), no_update, no_update, \
            html.Div("Nouvelle ligne en bas du tableau : saisissez sa maturité et son taux.", className="info"), no_update

    # Modification directe d'un point : on recalcule échéances et écarts
    if declencheur == "sx-points":
        return S.normaliser(points, d), no_update, no_update, None, no_update

    # Modification d'un ténor : résolution inverse
    if declencheur == "sx-tenors":
        df = S.vers_df(points)
        if len(df) < 2:
            raise PreventUpdate
        actuels = S.tableau_tenors(df, d)
        messages, erreur = [], False
        for ligne in tenors or []:
            t, cible = ligne.get("tenor"), pd.to_numeric(ligne.get("stresse"), errors="coerce")
            if t not in actuels or pd.isna(cible) or abs(float(cible) - actuels[t]) < 0.0005:
                continue
            try:
                points, msg = S.resoudre(points, t, float(cible), d, mode or "encadrants")
                messages.append(msg)
            except ValueError as e:
                messages.append(str(e))
                erreur = True
        if not messages:
            raise PreventUpdate
        return S.normaliser(points, d), no_update, no_update, \
            html.Div([html.Div(m) for m in messages], className="alerte" if erreur else "info"), no_update

    raise PreventUpdate


# ---------------------------------------------------------------------------
# Recalcul des ténors et graphiques
# ---------------------------------------------------------------------------

@callback(
    Output("sx-tenors", "data"), Output("sx-g-courbe", "figure"), Output("sx-g-delta", "figure"),
    Input("sx-points", "data"), Input("sx-orig", "data"), Input("ui", "data"), State("sx-date", "value"),
)
def recalculer(points, orig, ui, d):
    if not d or not orig:
        raise PreventUpdate
    df = S.vers_df(points or [])
    if len(df) < 2:
        raise PreventUpdate
    stresse = S.tableau_tenors(df, d)
    lignes = [{"tenor": t, "echeance": (pd.Timestamp(d) + timedelta(days=j)).strftime("%d/%m/%Y"),
               "orig": orig["tenors"][t], "stresse": stresse[t],
               "delta": round((stresse[t] - orig["tenors"][t]) * 100, 2)} for t, j in TENORS.items()]

    df_o = pd.DataFrame(orig["points"])
    j_o, y_o = S.courbe_fine(df_o, d)
    j_s, y_s = S.courbe_fine(df, d)
    fig = go.Figure()
    fig.add_scatter(x=j_o / 365.25, y=y_o, name="Origine (interpolée)", mode="lines",
                    line=dict(color=T.CIEL, width=2, dash="dot"),
                    hovertemplate="%{x:.2f} ans : %{y:.3f}%<extra>Origine</extra>")
    fig.add_scatter(x=j_s / 365.25, y=y_s, name="Stressée (interpolée)", mode="lines",
                    line=dict(color=T.NAVY, width=2.6),
                    hovertemplate="%{x:.2f} ans : %{y:.3f}%<extra>Stressée</extra>")
    fig.add_scatter(x=df_o["maturite_residuelle"] / 365.25, y=df_o["taux_moyen_pondere"], name="Points d'origine",
                    mode="markers", marker=dict(size=8, color=T.CIEL, symbol="circle-open", line=dict(width=2)),
                    hovertemplate="%{x:.2f} ans : %{y:.3f}%<extra>Point d'origine</extra>")
    modif = pd.DataFrame(points)
    modif = modif[modif["statut"].isin(["modifie", "nouveau"])] if "statut" in modif else modif.iloc[0:0]
    fig.add_scatter(x=df["maturite_residuelle"] / 365.25, y=df["taux_moyen_pondere"], name="Points stressés",
                    mode="markers", marker=dict(size=8, color=T.NAVY),
                    hovertemplate="%{x:.2f} ans : %{y:.3f}%<extra>Point stressé</extra>")
    if len(modif):
        fig.add_scatter(x=pd.to_numeric(modif["maturite"]) / 365.25, y=pd.to_numeric(modif["taux"]),
                        name="Points modifiés", mode="markers",
                        marker=dict(size=12, color=T.ROUGE, line=dict(color="#FFFFFF", width=1.5)),
                        hovertemplate="%{x:.2f} ans : %{y:.3f}%<extra>Point modifié</extra>")
    fig.add_scatter(x=np.array(list(TENORS.values())) / 365.25, y=[stresse[t] for t in TENORS], text=list(TENORS),
                    name="Ténors stressés", mode="markers+text", textposition="top center",
                    textfont=dict(size=10, color=T.GRIS),
                    marker=dict(size=9, color=T.VERT, symbol="diamond", line=dict(color="#FFFFFF", width=1)),
                    hovertemplate="%{text} : %{y:.3f}%<extra>Ténor stressé</extra>")
    fig = mise_en_page(fig, 420, yaxis_ticksuffix="%", hovermode="closest", xaxis_title="Maturité (années)",
                       xaxis_title_font=dict(size=11, color=T.GRIS), margin=dict(l=8, r=8, t=60, b=8))

    deltas = [l["delta"] for l in lignes]
    fig_d = go.Figure(go.Bar(x=list(TENORS), y=deltas,
                             marker_color=[T.ROUGE if v > 0 else T.VERT for v in deltas],
                             text=[f"{v:+.1f}" for v in deltas], textposition="outside",
                             textfont=dict(size=11, color=T.GRIS), hovertemplate="%{x} : %{y:+.1f} pb<extra></extra>"))
    fig_d.add_hline(y=0, line=dict(color="rgba(11,74,139,0.25)", width=1))
    amplitude = max([abs(v) for v in deltas] + [1.0])
    fig_d = mise_en_page(fig_d, 320, bargap=0.45, showlegend=False, yaxis_title="pb",
                         yaxis_title_font=dict(size=11, color=T.GRIS),
                         yaxis_range=[-amplitude * 1.3, amplitude * 1.3])
    return lignes, T.theme_fig(fig, ui), T.theme_fig(fig_d, ui)


@callback(Output("sx-dl", "data"), Output({"type": "sig-toast", "id": "stress-toast-0"}, "data"),
          Input("sx-export", "n_clicks"), State("sx-points", "data"), State("sx-tenors", "data"),
          State("sx-date", "value"), prevent_initial_call=True)
def exporter(n, points, tenors, d):
    if not n or not points:
        raise PreventUpdate
    pts = pd.DataFrame(points)[["echeance", "maturite", "taux_orig", "taux", "ecart"]].rename(columns={
        "echeance": "Échéance", "maturite": "Maturité (j)", "taux_orig": "Taux d'origine (%)",
        "taux": "Taux stressé (%)", "ecart": "Écart (pb)"})
    ten = pd.DataFrame(tenors or []).rename(columns={
        "tenor": "Ténor", "echeance": "Échéance", "orig": "Origine (%)", "stresse": "Stressé (%)", "delta": "Δ (pb)"})
    nom = f"stress_courbe_{(d or '').replace('-', '')}.xlsx"
    return dcc.send_bytes(db.exporter_classeur({"Points de la courbe": pts, "Ténors": ten}), nom), \
        toast("Stress test exporté en Excel.")
