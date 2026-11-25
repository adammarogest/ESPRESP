"""
Performance › ASFIM — tableaux des performances de l'ASFIM (téléchargés depuis fundshare.asfim.ma).

Onglet « Dernier tableau » : dernier tableau publié (quotidien ou hebdomadaire), filtres FGP /
CAPITALISANT / AN > 200 M (sauf ACTIONS), classement de chaque catégorie sur la performance courte
(1 jour ou 1 semaine) et le YTD avec la société de gestion de chaque OPCVM, place des fonds MAROGEST,
classements FONDS DÉDIÉS CAM et SECUR RENDEMENT (hebdomadaire), actif net par société de gestion.
Onglet « Deux dates » : deux tableaux hebdomadaires, performance de la VL sur la période, mêmes
classements, variation de l'actif par société de gestion et par catégorie.
"""
from __future__ import annotations

import base64
import io
from datetime import date, timedelta

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme, Sign
from dash.exceptions import PreventUpdate

import asfim as A
import theme as T
from composants import carte, fdate, kpi, mise_en_page, note, segment

dash.register_page(__name__, path="/performance/asfim", name="ASFIM", order=0, section="performance")

SG = A.SG_SUIVIE
F0 = Format(precision=0, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F1 = Format(precision=1, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ", decimal_delimiter=",")
F1S = Format(precision=1, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ", decimal_delimiter=",",
             sign=Sign.positive)
P2 = Format(precision=2, scheme=Scheme.percentage, decimal_delimiter=",")
P3 = Format(precision=3, scheme=Scheme.percentage, decimal_delimiter=",")
P2S = Format(precision=2, scheme=Scheme.percentage, decimal_delimiter=",", sign=Sign.positive)
P3S = Format(precision=3, scheme=Scheme.percentage, decimal_delimiter=",", sign=Sign.positive)
SURLIGNE = [{"if": {"filter_query": f'{{sg}} = "{SG}"'}, "backgroundColor": "rgba(226,0,26,0.07)", "fontWeight": 700},
            {"if": {"filter_query": f'{{sg}} = "{SG}"', "column_id": "opcvm"}, "color": "var(--rouge)"}]


def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def style_pa(textes: tuple, page: int = 25, cond=None) -> dict:
    return dict(
        style_as_list_view=True, style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600, "fontSize": "0.6rem",
                      "letterSpacing": "0.1em", "textTransform": "uppercase", "border": "none",
                      "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.55rem", "textAlign": "right",
                      "whiteSpace": "normal"},
        style_header_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in textes],
        style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                    "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.78rem",
                    "padding": "0.42rem 0.55rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in textes],
        style_data_conditional=(cond if cond is not None else SURLIGNE),
        sort_action="native", page_action="native", page_size=page, cell_selectable=False,
    )


def mm(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v / 1e6:,.1f} M".replace(",", " ").replace(".", ",")


def mds(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v / 1e9:,.2f} Mds MAD".replace(",", " ").replace(".", ",")


def pct(v, d=2, signe=True) -> str:
    if v is None or pd.isna(v):
        return "—"
    return (f"{v * 100:+.{d}f} %" if signe else f"{v * 100:.{d}f} %").replace(".", ",")


# ---------------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------------

def _corps_dernier():
    return html.Div(className="onglet-corps pile", children=[
        html.Div(className="carte barre", children=[
            html.Button("Télécharger le dernier tableau", id="pa-actualiser", className="btn"),
            html.Button("Exporter", id="pa-export", className="btn btn-secondaire"),
            dcc.Upload(id="pa-upload", accept=".xlsx", multiple=False, className="pa-upload",
                       children=html.Div(["Ou déposer un tableau ASFIM (.xlsx)"])),
            html.Div(id="pa-statut", className="pa-statut"),
        ]),
        dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id="pa-synthese")),
        carte("Classement par catégorie", "fonds MAROGEST surlignés · société de gestion de chaque OPCVM",
              html.Div(className="barre-interne pa-choix", children=[
                  champ("Catégorie", segment("pa-cat", ["MONÉTAIRE"], "MONÉTAIRE")),
                  champ("Classer sur", segment("pa-critere", [("Performance courte", "court"), ("YTD", "ytd")], "court")),
              ]),
              dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id="pa-categorie"))),
        dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id="pa-actif")),
    ])


def _corps_periode():
    return html.Div(className="onglet-corps pile", children=[
        html.Div(className="carte barre", children=[
            champ("Date de début (hebdomadaire)", dcc.Dropdown(id="pa-d1", options=[], clearable=False,
                                                                className="liste pa-date", maxHeight=300)),
            champ("Date de fin (hebdomadaire)", dcc.Dropdown(id="pa-d2", options=[], clearable=False,
                                                              className="liste pa-date", maxHeight=300)),
            html.Button("Comparer", id="pa-comparer", className="btn"),
            html.Button("Exporter", id="pa-export-periode", className="btn btn-secondaire"),
            html.Div(id="pa-statut-periode", className="pa-statut"),
        ]),
        html.Div(id="pa-msg-comparer", className="note"),
        dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id="pa-synthese-periode")),
        carte("Classement sur la période", "performance de la VL entre les deux dates · fonds MAROGEST surlignés",
              html.Div(className="barre-interne pa-choix", children=[
                  champ("Catégorie", segment("pa-cat-p", ["MONÉTAIRE"], "MONÉTAIRE")),
              ]),
              dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id="pa-categorie-periode"))),
        dcc.Loading(type="dot", color=T.NAVY, children=html.Div(id="pa-actif-periode")),
    ])


layout = html.Div(className="page", children=[
    dcc.Store(id="pa-tableau"), dcc.Store(id="pa-periode"),
    dcc.Download(id="pa-dl"), dcc.Download(id="pa-dl-p"),
    html.Div(className="entete", children=[
        html.Div("Performance", className="titre-fin"),
        html.Div("Tableaux des performances ASFIM : classements et actif net", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    dcc.Tabs(id="pa-onglets", value="dernier", className="onglets", children=[
        dcc.Tab(label="Dernier tableau", value="dernier", className="onglet", selected_className="onglet-actif",
                children=_corps_dernier()),
        dcc.Tab(label="Deux dates (hebdomadaires)", value="periode", className="onglet",
                selected_className="onglet-actif", children=_corps_periode()),
    ]),
    html.Div(className="note", children=[
        "Source : ", html.A("asfim.ma — Tableaux des performances", href=A.SITE, target="_blank"),
        ". Fichiers gardés dans le dossier « asfim » de l'application.",
    ]),
])


# ---------------------------------------------------------------------------
# Graphes et tableaux communs
# ---------------------------------------------------------------------------

def _fig_classement(r: pd.DataFrame, col: str, lib: str, ui):
    d = r.dropna(subset=[col]).sort_values(col, ascending=False)
    est = d["sg"].str.upper() == SG
    fig = go.Figure(go.Bar(
        x=list(range(1, len(d) + 1)), y=d[col] * 100,
        marker_color=np.where(est, T.ROUGE, T.CIEL), customdata=np.stack([d["opcvm"], d["sg"]], axis=1),
        hovertemplate="%{x}. %{customdata[0]}<br>%{customdata[1]}<br>%{y:.3f} %<extra></extra>"))
    k = 0
    for i, (_, f) in enumerate(d.iterrows(), start=1):
        if str(f["sg"]).upper() == SG:
            decal = 30 + 20 * (k % 3)                       # étiquettes décalées pour les fonds voisins
            fig.add_annotation(x=i, y=f[col] * 100, text=f"{f['opcvm']} ({i}e)", showarrow=True, arrowhead=0,
                               ay=-decal if f[col] >= 0 else decal, font=dict(size=10, color=T.ROUGE),
                               bgcolor="rgba(255,255,255,0.75)")
            k += 1
    mise_en_page(fig, 330, hovermode="closest", showlegend=False, yaxis_title=f"{lib} (%)", bargap=0.15)
    fig.update_xaxes(title_text="Rang", dtick=max(1, len(d) // 15))
    return T.theme_fig(fig, ui)


def _table_classement(r: pd.DataFrame, colonnes: list[tuple[str, str, Format]], tri: str):
    d = r.sort_values(f"rang_{tri}", na_position="last").copy()
    d["an_m"] = d["an"] / 1e6
    cols = [{"id": f"rang_{tri}", "name": "Rang", "type": "numeric", "format": F0},
            {"id": "opcvm", "name": "OPCVM"}, {"id": "sg", "name": "Société de gestion"},
            {"id": "an_m", "name": "AN (M MAD)", "type": "numeric", "format": F1}]
    for c, lib, fmt in colonnes:
        cols.append({"id": c, "name": lib, "type": "numeric", "format": fmt})
        if c != tri:
            cols.append({"id": f"rang_{c}", "name": f"Rang {lib}", "type": "numeric", "format": F0})
    ids = [c["id"] for c in cols]
    return dash_table.DataTable(data=d[ids].to_dict("records"), columns=cols, **style_pa(("opcvm", "sg"), 25))


def _table_synthese(s: pd.DataFrame, colonnes: list[tuple[str, str, Format]]):
    if s.empty:
        return note(f"Aucun fonds {SG} dans les classements.")
    cols = [{"id": "categorie", "name": "Classement"}, {"id": "opcvm", "name": f"Fonds {SG}"},
            {"id": "an_m", "name": "AN (M MAD)", "type": "numeric", "format": F1}]
    for c, lib, fmt in colonnes:
        cols += [{"id": c, "name": lib, "type": "numeric", "format": fmt}, {"id": f"rang_{c}_txt", "name": f"Rang {lib}"}]
    d = s.assign(an_m=s["an"] / 1e6)
    return dash_table.DataTable(data=d[[c["id"] for c in cols]].to_dict("records"), columns=cols,
                                **style_pa(("categorie", "opcvm") + tuple(f"rang_{c}_txt" for c, _, _ in colonnes),
                                           30, cond=[]))


def _absents(r: pd.DataFrame):
    a = r.attrs.get("absents") or []
    return note("Fonds de la liste absents du tableau : " + ", ".join(a) + ".") if a else None


# ---------------------------------------------------------------------------
# Dernier tableau
# ---------------------------------------------------------------------------

@callback(
    Output("pa-tableau", "data"), Output("pa-statut", "children"),
    Input("pa-actualiser", "n_clicks"), Input("pa-upload", "contents"), Input("pa-tableau", "id"),
    State("pa-upload", "filename"),
)
def charger(_n, contenu, _montage, nom):
    try:
        if ctx.triggered_id == "pa-upload" and contenu:
            octets = base64.b64decode(contenu.split(",", 1)[1])
            chemin = A.enregistrer_manuel(octets)
            t = A.lire(chemin)
            source = f"fichier chargé ({nom})"
        else:
            d, hebdo = A.dernier_tableau()
            chemin = A.telecharger(d, hebdo)
            t = A.lire(chemin)
            source = "téléchargé depuis asfim.ma"
    except Exception as e:
        locaux = A.liste_locale()
        if locaux.empty:
            return no_update, html.Span(f"Téléchargement impossible ({e}). Déposez le fichier du site à la main.",
                                        className="alerte")
        l = locaux.iloc[0]
        chemin = A.chemin_cache(l["date"], bool(l["is_hebdo"]))
        t = A.lire(chemin)
        source = f"dernier fichier du dossier asfim (site injoignable : {str(e)[:80]})"
    badge = html.Span("Hebdomadaire" if t["hebdo"] else "Quotidien",
                      className="pa-badge " + ("hebdo" if t["hebdo"] else "quotidien"))
    return ({"chemin": str(chemin), "date": t["date"].isoformat(), "hebdo": t["hebdo"]},
            html.Span([badge, f" Tableau du {t['date']:%d/%m/%Y} · {len(t['data'])} OPCVM · {source}"]))


@callback(Output("pa-cat", "options"), Output("pa-cat", "value"),
          Input("pa-tableau", "data"), State("pa-cat", "value"))
def options_categories(info, actuelle):
    if not info:
        raise PreventUpdate
    cl = A.classements(A.lire(info["chemin"]))
    opts = [{"label": k.title() if k not in ("OCT", "OMLT") else k, "value": k} for k in cl]
    return opts, actuelle if actuelle in cl else next(iter(cl))


@callback(Output("pa-synthese", "children"), Input("pa-tableau", "data"), Input("ui", "data"))
def synthese(info, ui):
    if not info:
        return note("Téléchargement du dernier tableau…")
    t = A.lire(info["chemin"])
    c, lib = A.col_courte(t["hebdo"])
    cl = A.classements(t)
    s = A.synthese_sg(cl, [c, "ytd"])
    fmt_c = P3S
    nb_filtres = sum(len(v) for k, v in cl.items() if k in A.CATEGORIES)
    d = t["data"]
    kpis = html.Div(className="kpi-grille", children=[
        kpi("Tableau", f"{t['date']:%d/%m/%Y}", "hebdomadaire" if t["hebdo"] else "quotidien"),
        kpi("OPCVM du tableau", f"{len(d)}", f"{nb_filtres} retenus après filtres"),
        kpi(f"Fonds {SG} classés", f"{len(s)}", f"dans {s['categorie'].nunique() if len(s) else 0} classement(s)"),
        kpi(f"Actif net {SG}", mds(d.loc[d['sg'].str.upper() == SG, 'an'].sum()),
            f"{d.loc[d['sg'].str.upper() == SG].shape[0]} OPCVM au tableau"),
    ])
    regle = note(f"Filtres : Souscripteurs = FGP, Affectation des résultats = CAPITALISANT, AN > 200 M MAD "
                 f"(sans filtre d'actif pour ACTIONS). Performance courte : {lib}"
                 + (" (la colonne 1 jour est vide dans les tableaux hebdomadaires)." if t["hebdo"] else ".")
                 + (" FONDS DÉDIÉS CAM et SECUR RENDEMENT : listes fixes, sans filtre." if t["hebdo"] else ""))
    return carte(f"Place des fonds {SG}", f"tableau {'hebdomadaire' if t['hebdo'] else 'quotidien'} du "
                                           f"{t['date']:%d/%m/%Y} · rang / nombre de fonds classés",
                 kpis, regle, _table_synthese(s, [(c, lib, fmt_c), ("ytd", "YTD", P2S)]))


@callback(Output("pa-categorie", "children"),
          Input("pa-tableau", "data"), Input("pa-cat", "value"), Input("pa-critere", "value"), Input("ui", "data"))
def categorie(info, cat, critere, ui):
    if not info or not cat:
        raise PreventUpdate
    t = A.lire(info["chemin"])
    c, lib = A.col_courte(t["hebdo"])
    cl = A.classements(t)
    if cat not in cl:
        return note("Catégorie absente de ce tableau.")
    r = cl[cat]
    col, lib_col = (c, lib) if critere == "court" else ("ytd", "YTD")
    if r.empty:
        return note("Aucun fonds après filtres dans cette catégorie.")
    fmt_c = P3S
    return html.Div([
        html.Div(f"{len(r)} fonds classés · {cat}", className="note"),
        dcc.Graph(figure=_fig_classement(r, col, lib_col, ui), config={"displayModeBar": False}),
        _table_classement(r, [(c, lib, fmt_c), ("ytd", "YTD", P2S)], col),
        _absents(r),
    ])


def _vue_actif(an: dict, cat: str, titre: str, sous: str, ui):
    p = an["an"]
    rangs, parts = an["rangs"], an["parts"]
    tab = (p / 1e6).reset_index()
    cols = [{"id": "sg", "name": "Société de gestion"}] + [
        {"id": c, "name": c, "type": "numeric", "format": F1} for c in p.columns]
    rg = rangs.loc[SG] if SG in rangs.index else None
    resume = (note(f"{SG} : " + " · ".join(f"{c.title() if c not in ('OCT', 'OMLT', 'TOTAL') else c} "
                                           f"{int(rg[c])}e" for c in p.columns if pd.notna(rg[c])))
              if rg is not None else None)
    col = cat if cat in p.columns else "TOTAL"
    d = p[col][p[col] > 0].sort_values(ascending=True)
    fig = go.Figure(go.Bar(x=d / 1e9, y=d.index, orientation="h",
                           marker_color=[T.ROUGE if i.upper() == SG else T.NAVY for i in d.index],
                           customdata=np.stack([parts.loc[d.index, col] * 100, rangs.loc[d.index, col]], axis=1),
                           hovertemplate="%{y}<br>%{x:,.2f} Mds MAD · part %{customdata[0]:.1f} % · "
                                         "rang %{customdata[1]:.0f}<extra></extra>"))
    mise_en_page(fig, max(300, 24 * len(d) + 60), hovermode="closest", showlegend=False, xaxis_title="Mds MAD")
    cl = pd.DataFrame({"sg": d.index[::-1], "an_m": (d[::-1] / 1e6).values,
                       "part": parts.loc[d.index[::-1], col].values, "rang": rangs.loc[d.index[::-1], col].values})
    cols_cl = [{"id": "rang", "name": "Rang", "type": "numeric", "format": F0}, {"id": "sg", "name": "Société de gestion"},
               {"id": "an_m", "name": f"AN {col} (M MAD)", "type": "numeric", "format": F1},
               {"id": "part", "name": "Part de marché", "type": "numeric", "format": P2}]
    return carte(titre, sous, resume,
                 html.Div(f"Classement des sociétés de gestion · {col} (suit la catégorie choisie plus haut)",
                          className="note"),
                 html.Div(className="grille-2", children=[
                     dcc.Graph(figure=T.theme_fig(fig, ui), config={"displayModeBar": False}),
                     dash_table.DataTable(data=cl.to_dict("records"), columns=cols_cl, **style_pa(("sg",), 25)),
                 ]),
                 html.Div("AN par société de gestion et par catégorie (M MAD)", className="note"),
                 dash_table.DataTable(data=tab.to_dict("records"), columns=cols, **style_pa(("sg",), 25)))


@callback(Output("pa-actif", "children"), Input("pa-tableau", "data"), Input("pa-cat", "value"), Input("ui", "data"))
def actif(info, cat, ui):
    if not info:
        raise PreventUpdate
    t = A.lire(info["chemin"])
    an = A.actif_net(t["data"])
    sous = ("AN géré par société de gestion et par catégorie (M MAD), tous les OPCVM du tableau"
            + ("" if t["hebdo"] else " — tableau quotidien : seuls les fonds à VL quotidienne y figurent"))
    return _vue_actif(an, cat, "Actif net par société de gestion", sous, ui)


# ---------------------------------------------------------------------------
# Deux dates hebdomadaires
# ---------------------------------------------------------------------------

@callback(Output("pa-d1", "options"), Output("pa-d1", "value"), Output("pa-d2", "options"), Output("pa-d2", "value"),
          Output("pa-statut-periode", "children"), Input("pa-d1", "id"))
def dates_hebdo(_montage):
    try:
        l = A.liste_tableaux(True, 160)
        l = l[l["is_hebdo"]]
        msg = ""
    except Exception as e:
        l = A.liste_locale()
        l = l[l["is_hebdo"]]
        msg = html.Span(f"Liste du site indisponible ({str(e)[:80]}) : dates déjà téléchargées seulement.",
                        className="alerte")
    if l.empty:
        return [], None, [], None, msg or html.Span("Aucun tableau hebdomadaire disponible.", className="alerte")
    opts = [{"label": f"{d:%d/%m/%Y}", "value": d.isoformat()} for d in l["date"]]
    d2 = l["date"].iloc[0]
    debut = l[l["date"] <= d2 - timedelta(days=28)]
    d1 = debut["date"].iloc[0] if len(debut) else l["date"].iloc[-1]
    return opts, d1.isoformat(), opts, d2.isoformat(), msg


@callback(Output("pa-periode", "data"), Output("pa-msg-comparer", "children"),
          Input("pa-comparer", "n_clicks"), State("pa-d1", "value"), State("pa-d2", "value"), prevent_initial_call=True)
def comparer(n, d1, d2):
    if not n or not d1 or not d2:
        raise PreventUpdate
    d1, d2 = sorted([date.fromisoformat(d1), date.fromisoformat(d2)])
    if d1 == d2:
        return no_update, html.Span("Choisissez deux dates différentes.", className="alerte")
    try:
        c1, c2 = A.telecharger(d1, True), A.telecharger(d2, True)
        t1, t2 = A.lire(c1), A.lire(c2)
    except Exception as e:
        return no_update, html.Span(f"Téléchargement impossible : {e}", className="alerte")
    if not (t1["hebdo"] and t2["hebdo"]):
        return no_update, html.Span("Les deux dates doivent être des tableaux hebdomadaires.", className="alerte")
    return ({"c1": str(c1), "c2": str(c2), "d1": d1.isoformat(), "d2": d2.isoformat()},
            f"Période du {d1:%d/%m/%Y} au {d2:%d/%m/%Y} ({(d2 - d1).days} jours) : tableaux hebdomadaires chargés.")


def _periode(info):
    t1, t2 = A.lire(info["c1"]), A.lire(info["c2"])
    m = A.apparier(t1, t2)
    return t1, t2, m


@callback(Output("pa-cat-p", "options"), Output("pa-cat-p", "value"),
          Input("pa-periode", "data"), State("pa-cat-p", "value"))
def options_categories_p(info, actuelle):
    if not info:
        raise PreventUpdate
    _, _, m = _periode(info)
    cl = A.classements_periode(m)
    opts = [{"label": k.title() if k not in ("OCT", "OMLT") else k, "value": k} for k in cl]
    return opts, actuelle if actuelle in cl else next(iter(cl))


@callback(Output("pa-synthese-periode", "children"), Input("pa-periode", "data"), Input("ui", "data"))
def synthese_periode(info, ui):
    if not info:
        return note("Choisissez deux dates hebdomadaires puis « Comparer ».")
    t1, t2, m = _periode(info)
    cl = A.classements_periode(m)
    s = A.synthese_sg(cl, ["perf"])
    apparies = int(m["vl1"].notna().sum())
    kpis = html.Div(className="kpi-grille", children=[
        kpi("Période", f"{t1['date']:%d/%m/%Y} → {t2['date']:%d/%m/%Y}", f"{(t2['date'] - t1['date']).days} jours"),
        kpi("OPCVM appariés", f"{apparies}", f"sur {len(m)} au tableau de fin"),
        kpi(f"Fonds {SG} classés", f"{len(s)}", f"dans {s['categorie'].nunique() if len(s) else 0} classement(s)"),
        kpi(f"Actif net {SG}", mds(m.loc[m['sg'].str.upper() == SG, 'an2'].sum()),
            "variation " + mds(m.loc[m['sg'].str.upper() == SG, 'var_an'].sum()).replace(" Mds", " Mds")),
    ])
    regle = note("Performance = VL de fin / VL de début − 1 (fonds présents aux deux dates). Mêmes filtres que le "
                 "dernier tableau, appliqués aux caractéristiques et à l'actif de la date de fin ; FONDS DÉDIÉS CAM et "
                 "SECUR RENDEMENT sans filtre.")
    return carte(f"Place des fonds {SG} sur la période", None, kpis, regle,
                 _table_synthese(s, [("perf", "Perf. période", P2S)]))


@callback(Output("pa-categorie-periode", "children"),
          Input("pa-periode", "data"), Input("pa-cat-p", "value"), Input("ui", "data"))
def categorie_periode(info, cat, ui):
    if not info or not cat:
        raise PreventUpdate
    _, _, m = _periode(info)
    cl = A.classements_periode(m)
    if cat not in cl or cl[cat].empty:
        return note("Aucun fonds classé dans cette catégorie.")
    r = cl[cat]
    return html.Div([
        html.Div(f"{len(r)} fonds classés · {cat}", className="note"),
        dcc.Graph(figure=_fig_classement(r, "perf", "Performance sur la période", ui), config={"displayModeBar": False}),
        _table_classement(r, [("perf", "Perf. période", P3S), ("ytd", "YTD (fin)", P2S)], "perf"),
        _absents(r),
    ])


@callback(Output("pa-actif-periode", "children"), Input("pa-periode", "data"), Input("ui", "data"))
def actif_periode(info, ui):
    if not info:
        raise PreventUpdate
    t1, t2, _ = _periode(info)
    v = A.variation_actif(t1, t2)
    sg = v["sg"].reset_index().rename(columns={"index": "sg"})
    for c in ("an1", "an2", "variation"):
        sg[c] = sg[c] / 1e6
    cols_sg = [{"id": "sg", "name": "Société de gestion"},
               {"id": "an1", "name": f"AN {t1['date']:%d/%m/%Y} (M)", "type": "numeric", "format": F1},
               {"id": "an2", "name": f"AN {t2['date']:%d/%m/%Y} (M)", "type": "numeric", "format": F1},
               {"id": "variation", "name": "Variation (M)", "type": "numeric", "format": F1S},
               {"id": "variation_pct", "name": "Variation", "type": "numeric", "format": P2S},
               {"id": "part2", "name": "Part de marché", "type": "numeric", "format": P2},
               {"id": "var_part_pb", "name": "Var. part (pb)", "type": "numeric", "format": F1S},
               {"id": "rang1", "name": "Rang début", "type": "numeric", "format": F0},
               {"id": "rang2", "name": "Rang fin", "type": "numeric", "format": F0}]
    cond = SURLIGNE + [{"if": {"filter_query": "{variation} < 0", "column_id": "variation"}, "color": "var(--rouge)"},
                       {"if": {"filter_query": "{variation} > 0", "column_id": "variation"}, "color": "var(--vert)"}]
    cat = v["categorie"].reset_index().rename(columns={"index": "categorie"})
    for c in ("an1", "an2", "variation"):
        cat[c] = cat[c] / 1e6
    cols_cat = [{"id": "categorie", "name": "Catégorie"}] + cols_sg[1:7]
    cr = (v["croise"] / 1e6).reset_index()
    cols_cr = [{"id": "sg", "name": "Société de gestion"}] + [
        {"id": c, "name": c, "type": "numeric", "format": F1S} for c in v["croise"].columns]

    d = v["sg"].sort_values("variation")
    fig = go.Figure(go.Bar(x=d["variation"] / 1e6, y=d.index, orientation="h",
                           marker_color=[T.ROUGE if i.upper() == SG else (T.VERT if x >= 0 else "#F07A86")
                                         for i, x in zip(d.index, d["variation"])],
                           hovertemplate="%{y}<br>%{x:+,.1f} M MAD<extra></extra>"))
    mise_en_page(fig, max(300, 24 * len(d) + 60), hovermode="closest", showlegend=False, xaxis_title="Variation (M MAD)")
    dc = v["categorie"]
    fig2 = go.Figure(go.Bar(x=dc.index, y=dc["variation"] / 1e6,
                            marker_color=[T.VERT if x >= 0 else "#F07A86" for x in dc["variation"]],
                            text=[pct(x) for x in dc["variation_pct"]], textposition="outside",
                            hovertemplate="%{x}<br>%{y:+,.1f} M MAD<extra></extra>"))
    mise_en_page(fig2, 300, hovermode="closest", showlegend=False, yaxis_title="Variation (M MAD)")
    return html.Div(className="pile", children=[
        carte("Évolution de l'actif net par société de gestion", "tous les OPCVM des deux tableaux",
              dash_table.DataTable(data=sg.to_dict("records"), columns=cols_sg, **style_pa(("sg",), 25, cond)),
              dcc.Graph(figure=T.theme_fig(fig, ui), config={"displayModeBar": False})),
        carte("Évolution de l'actif net par catégorie", None,
              html.Div(className="grille-2", children=[
                  dcc.Graph(figure=T.theme_fig(fig2, ui), config={"displayModeBar": False}),
                  dash_table.DataTable(data=cat.to_dict("records"), columns=cols_cat,
                                       **style_pa(("categorie",), 10, cond[2:])),
              ])),
        carte("Variation de l'actif par société de gestion et par catégorie", "M MAD",
              dash_table.DataTable(data=cr.to_dict("records"), columns=cols_cr, **style_pa(("sg",), 25))),
    ])


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def _excel(feuilles: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for nom, df in feuilles.items():
            df.to_excel(xw, sheet_name=nom[:31], index=not isinstance(df.index, pd.RangeIndex))
    return buf.getvalue()


def _feuilles_classements(cl: dict, colonnes: list[str]) -> dict:
    garder = ["opcvm", "sg", "categorie", "souscripteurs", "affectation", "an", *colonnes,
              *[f"rang_{c}" for c in colonnes]]
    return {k: v[[c for c in garder if c in v]] for k, v in cl.items()}


@callback(Output("pa-dl-p", "data"), Input("pa-export-periode", "n_clicks"), State("pa-periode", "data"),
          prevent_initial_call=True)
def exporter_periode(n, info):
    if not n or not info:
        raise PreventUpdate
    t1, t2, m = _periode(info)
    cl = A.classements_periode(m)
    v = A.variation_actif(t1, t2)
    feuilles = {"Synthèse MAROGEST": A.synthese_sg(cl, ["perf"]), **_feuilles_classements(cl, ["perf", "ytd"]),
                "AN par SG": v["sg"], "AN par catégorie": v["categorie"], "Var SG x catégorie": v["croise"]}
    return dcc.send_bytes(_excel(feuilles), f"asfim_periode_{t1['date']:%Y%m%d}_{t2['date']:%Y%m%d}.xlsx")


@callback(Output("pa-dl", "data"), Input("pa-export", "n_clicks"), State("pa-tableau", "data"), prevent_initial_call=True)
def exporter(n, info):
    if not n or not info:
        raise PreventUpdate
    t = A.lire(info["chemin"])
    c, _ = A.col_courte(t["hebdo"])
    cl = A.classements(t)
    an = A.actif_net(t["data"])
    feuilles = {"Synthèse MAROGEST": A.synthese_sg(cl, [c, "ytd"]), **_feuilles_classements(cl, [c, "ytd"]),
                "AN par SG": an["an"], "Rangs AN": an["rangs"], "Parts de marché": an["parts"]}
    return dcc.send_bytes(_excel(feuilles), f"asfim_{'hebdo' if t['hebdo'] else 'quotidien'}_{t['date']:%Y%m%d}.xlsx")
