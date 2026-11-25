"""Analyse de titre — Contrôle de valorisation : rapprochement d'un export VL avec le pricer."""

from __future__ import annotations

import time

import base64
from datetime import date

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme, Sign
from dash.exceptions import PreventUpdate

import base_courbe as db
import base_titre as BT
import controle as C
import pricer as PR
import swap as SW
import theme as T
from composants import carte, etiquette, fdate, graphe, kpi, mise_en_page, note, segment, table, toast, signaux

dash.register_page(__name__, path="/controle", name="Contrôle de valorisation", order=30, section="titre")

F2 = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ")
F2S = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, group_delimiter=" ", sign=Sign.positive)
F6 = Format(precision=6, scheme=Scheme.fixed)
F6S = Format(precision=6, scheme=Scheme.fixed, sign=Sign.positive)
F3S = Format(precision=3, scheme=Scheme.fixed, sign=Sign.positive)

COULEURS_STATUT = {"Conforme": T.VERT, "Écart de prix": T.ROUGE, "Écart de sensibilité": "#E0A100",
                   "Non comparées": T.GRIS_CLAIR}
FILTRES = [("Mal pricées", "mal"), ("Toutes", "toutes"), ("Conformes", "ok"), ("Non comparées", "non")]

COLONNES = [
    {"id": "code", "name": "Code"}, {"id": "description", "name": "Description"},
    {"id": "categorie", "name": "Cat."}, {"id": "type_lib", "name": "Type"},
    {"id": "valo_fichier", "name": "Valo fichier", "type": "numeric", "format": F2},
    {"id": "valo_calcul", "name": "Valo calculée", "type": "numeric", "format": F2},
    {"id": "ecart_mad", "name": "Écart (MAD)", "type": "numeric", "format": F2S},
    {"id": "ecart_pb", "name": "Écart (pb)", "type": "numeric", "format": F3S},
    {"id": "sensi_fichier", "name": "Sensi fichier", "type": "numeric", "format": F6},
    {"id": "sensi_calcul", "name": "Sensi calculée", "type": "numeric", "format": F6},
    {"id": "ecart_sensi", "name": "Écart sensi", "type": "numeric", "format": F6S},
    {"id": "statut_lib", "name": "Statut"}, {"id": "cause", "name": "Cause probable"},
]

STYLE_TABLE = dict(
    style_as_list_view=True, style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "transparent", "color": "var(--gris)", "fontWeight": 600, "fontSize": "0.6rem",
                  "letterSpacing": "0.1em", "textTransform": "uppercase", "border": "none",
                  "borderBottom": "1px solid var(--trait)", "padding": "0.5rem 0.55rem", "textAlign": "right",
                  "whiteSpace": "normal"},
    style_header_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                              for c in ("code", "description", "categorie", "type_lib", "statut_lib", "cause")],
    style_cell={"backgroundColor": "transparent", "color": "var(--encre)", "border": "none",
                "borderBottom": "1px solid var(--trait-doux)", "fontFamily": "inherit", "fontSize": "0.78rem",
                "padding": "0.42rem 0.55rem", "textAlign": "right", "fontVariantNumeric": "tabular-nums"},
    style_cell_conditional=[
        {"if": {"column_id": "code"}, "textAlign": "left", "fontWeight": 600, "color": "var(--navy)"},
        {"if": {"column_id": "description"}, "textAlign": "left", "maxWidth": "220px", "overflow": "hidden",
         "textOverflow": "ellipsis", "whiteSpace": "nowrap"},
        {"if": {"column_id": "categorie"}, "textAlign": "left"}, {"if": {"column_id": "type_lib"}, "textAlign": "left"},
        {"if": {"column_id": "statut_lib"}, "textAlign": "left", "fontWeight": 600},
        {"if": {"column_id": "cause"}, "textAlign": "left", "minWidth": "320px", "width": "320px", "maxWidth": "420px",
         "whiteSpace": "normal", "fontSize": "0.72rem", "color": "var(--gris)", "lineHeight": "1.3"},
    ],
    style_data_conditional=[
        {"if": {"filter_query": '{statut_lib} = "Conforme"', "column_id": "statut_lib"}, "color": "var(--vert)"},
        {"if": {"filter_query": '{statut_lib} = "Écart de prix"', "column_id": "statut_lib"}, "color": "var(--rouge)"},
        {"if": {"filter_query": '{statut_lib} = "Écart de sensibilité"', "column_id": "statut_lib"}, "color": "#C98F00"},
        {"if": {"filter_query": "{ecart_mad} > 0.05 || {ecart_mad} < -0.05", "column_id": "ecart_mad"},
         "color": "var(--rouge)", "fontWeight": 700},
    ],
    tooltip_delay=300, tooltip_duration=None,
    sort_action="native", page_action="native", page_size=20, row_selectable="single", cell_selectable=False,
)


def champ(lib, comp, classe=""):
    return html.Div(className=f"champ {classe}", children=[html.Label(lib), comp])


def jour_fr(id_):
    return dcc.DatePickerSingle(id=id_, display_format="DD/MM/YYYY", placeholder="JJ/MM/AAAA", first_day_of_week=1,
                                month_format="MMMM YYYY", className="date-fr")


def mad(v, signe=False):
    if v is None or pd.isna(v):
        return "—"
    return (f"{v:+,.2f}" if signe else f"{v:,.2f}").replace(",", " ")


layout = html.Div(className="page", children=[
    dcc.Store(id="cv-fichier"), dcc.Download(id="cv-dl"),
    html.Div(className="entete", children=[
        html.Div("Contrôle de valorisation", className="titre-fin"),
        html.Div("Rapprochement de l'export VL avec le pricer", className="titre-gras"),
        html.Div(className="titre-trait"),
    ]),
    html.Div(className="carte barre", children=[
        dcc.Upload(id="cv-upload", accept=".xls,.xlsx", multiple=False, className="depot", children=html.Div([
            html.Span("Déposer l'export de valorisation", className="depot-titre"),
            html.Span(".xls ou .xlsx — ou cliquer pour parcourir", className="depot-sous")])),
        champ("Date de la courbe", jour_fr("cv-courbe")),
        champ("Date de valorisation", jour_fr("cv-valo")),
        champ("Tolérance prix (MAD / titre)", dcc.Input(id="cv-tol-prix", type="number", value=0.05, min=0, step="any",
                                                        debounce=True, className="input-date input-champ input-court")),
        champ("Tolérance sensi", dcc.Input(id="cv-tol-sensi", type="number", value=0.0001, min=0, step="any",
                                           debounce=True, className="input-date input-champ input-court")),
        html.Button("Exporter", id="cv-export", className="btn btn-secondaire"),
    ]),
    html.Div(id="cv-info", className="note"),
    html.Div(className="ligne-boutons", children=[
        html.Button("Télécharger la courbe depuis BKAM", id="cv-bkam", className="btn btn-lien", style={"display": "none"}),
    ]),
    html.Div(id="cv-contenu", style={"display": "none"}, children=[
        html.Div(className="carte", children=[html.Div(id="cv-kpi", className="kpi-grille kpi-6")]),
        html.Div(className="grille-2 grille-backtest", children=[
            carte("Résumé par catégorie", "écarts en pb du prix, lignes comparées",
                  html.Div(id="cv-categories", className="tableau-defile")),
            carte("Statut par catégorie", None, graphe("cv-g-statut", 300)),
        ]),
        carte("Écarts de prix", "en pb du prix, par maturité résiduelle ; au-delà de ±10 pb, le point est placé en "
              "bordure (survol : valeur réelle)", graphe("cv-g-ecarts", 300)),
        carte("Lignes", "cochez une ligne pour le détail du calcul", html.Div(id="cv-n", className="note"),
              dash_table.DataTable(id="cv-table", columns=COLONNES, data=[], **STYLE_TABLE),
              outils=segment("cv-filtre", FILTRES, "mal")),
        html.Div(id="cv-detail"),
    ]),
    carte("Méthode", None, note(
        "Chaque ligne de l'export est cherchée par son code dans base_titre.xlsx et valorisée avec les règles du "
        "Pricer, sur la courbe BKAM de la date choisie. Par défaut : courbe = date de référence du fichier, "
        "valorisation = jour ouvré suivant (convention constatée : J+1).",
        "Conventions reproduites : coupon en exact/360 quand METHODE_COUPON est en x/360 ou que le coupon est "
        "infra-annuel ; révisable à moins d'un an de sa révision valorisé au taux monétaire jusqu'à la révision ; "
        "délai de grâce DECALAG_AMORT ; duration monétaire = sensibilité × (1 + taux). Les amortissables sont "
        "supposés linéaires : un échéancier réel différent (FPCT notamment) apparaît en écart de capital.",
        "Une ligne est conforme si l'écart de prix unitaire et l'écart de sensibilité restent sous les tolérances. "
        "La cause probable est un diagnostic automatique (rapport de capital, ≈ 5 jours de coupon, petit écart…).")),
])
layout.children.append(signaux(['controle-toast-0', 'controle-toast-1', 'controle-toast-2', 'controle-maj-3']))


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------

def _export(fichier):
    if not fichier:
        raise PreventUpdate
    contenu = base64.b64decode(fichier["contenu"])
    return C.enregistrer_export(contenu, fichier["nom"])


def _etiquette_depot(nom=None, n=None):
    if not nom:
        return html.Div([html.Span("Déposer l'export de valorisation", className="depot-titre"),
                         html.Span(".xls ou .xlsx — ou cliquer pour parcourir", className="depot-sous")])
    return html.Div([html.Span(nom, className="depot-titre"),
                     html.Span(f"{n} lignes — déposer un autre fichier pour le remplacer", className="depot-sous")])


@callback(Output("cv-upload", "children"), Input("cv-fichier", "data"))
def depot(fichier):
    if not fichier:
        return _etiquette_depot()
    _, ex = _export(fichier)
    return _etiquette_depot(fichier["nom"], len(ex))


@callback(Output("cv-fichier", "data"), Output("cv-courbe", "date"), Output("cv-valo", "date"),
          Output({"type": "sig-toast", "id": "controle-toast-0"}, "data"),
          Input("cv-upload", "contents"), State("cv-upload", "filename"), prevent_initial_call=True)
def charger(contents, nom):
    if not contents:
        raise PreventUpdate
    contenu = base64.b64decode(contents.split(",", 1)[1])
    try:
        _, ex = C.enregistrer_export(contenu, nom or "export.xlsx")
    except Exception as e:                                                   # noqa: BLE001
        return no_update, no_update, no_update, toast(f"Lecture impossible : {e}", "erreur")
    d0 = C.date_reference(ex)
    fichier = {"nom": nom, "contenu": base64.b64encode(contenu).decode()}
    if d0 is None:
        return fichier, no_update, no_update, toast(f"{nom} : {len(ex)} lignes, sans date de référence.")
    return fichier, d0.isoformat(), C.jour_ouvre_suivant(d0).isoformat(), \
        toast(f"{nom} : {len(ex)} lignes, référence du {d0:%d/%m/%Y}.")


@callback(Output("cv-info", "children"), Output("cv-contenu", "style"), Output("cv-bkam", "style"),
          Output("cv-kpi", "children"), Output("cv-categories", "children"), Output("cv-g-statut", "figure"),
          Output("cv-g-ecarts", "figure"),
          Input("cv-fichier", "data"), Input("cv-valo", "date"), Input("cv-courbe", "date"),
          Input("cv-tol-prix", "value"), Input("cv-tol-sensi", "value"), Input("maj", "data"), Input("ui", "data"))
def calculer(fichier, valo, courbe, tol_prix, tol_sensi, _, ui):
    cache = {"display": "none"}
    if not fichier:
        return "Déposez un export de valorisation pour lancer le contrôle.", cache, cache, *[no_update] * 4
    if not (valo and courbe):
        raise PreventUpdate
    cle, ex = _export(fichier)
    df, info = C.comparer(cle, valo, courbe, tol_prix or 0, tol_sensi or 0)
    d0 = C.date_reference(ex)
    entete = f"{fichier['nom']} · {len(ex)} lignes · référence {fdate(d0) if d0 else '—'} · valorisation au {fdate(valo)} · "
    if df.empty:
        return entete + info, cache, {}, *[no_update] * 4
    R = C.resume(df)
    kpis = [
        kpi("Lignes du fichier", f"{R['n']}", f"{R['comparees']} comparées"),
        kpi("Conformes", f"{R['ok']}", f"{R['taux_ok']:.1%} des lignes comparées" if pd.notna(R["taux_ok"]) else ""),
        kpi("Écarts de prix", f"{R['prix']}", f"tolérance {tol_prix or 0:g} MAD par titre", T.ROUGE if R["prix"] else None),
        kpi("Écarts de sensibilité", f"{R['sensi']}", f"prix conforme, tolérance {tol_sensi or 0:g}"),
        kpi("Non comparées", f"{R['non_comparees']}", "non valorisées, absentes ou échues"),
        kpi("Écart médian", f"{R['ecart_med_pb']:.3f} pb", "en valeur absolue, lignes comparées"),
    ]
    cat = C.par_categorie(df)
    spec = [{"col": c, "lib": c, "genre": "int" if "pb" not in c else "n3", "cls": "j" if c == "Conformes" else ""}
            for c in cat.columns]
    t_cat = table(cat, spec, premiere="Catégorie", dense="dense",
                  styles=lambda i, c, v: {"fontWeight": 700} if i == "Total" else
                  ({"color": "var(--rouge)", "fontWeight": 700} if c in ("Écarts de prix", "Écarts de sensi") and v else None))

    g1 = go.Figure()
    groupes = df.assign(groupe=np.where(df["statut"].isin(C.NON_COMPAREES), "Non comparées", df["statut_lib"]))
    comptes = groupes.groupby(["categorie", "groupe"]).size().unstack(fill_value=0)
    for g, coul in COULEURS_STATUT.items():
        if g in comptes.columns:
            g1.add_bar(x=comptes.index, y=comptes[g], name=g, marker_color=coul,
                       hovertemplate="%{x} : %{y} lignes<extra>" + g + "</extra>")
    g1 = mise_en_page(g1, 300, barmode="stack", bargap=0.4, legend=dict(orientation="h", y=1.12, x=0))

    comp = df[df["statut"].isin(("ok",) + C.MAL_PRICES)].copy()
    comp["mat"] = [(pd.Timestamp(e) - pd.Timestamp(valo)).days / 365.25 if pd.notna(e) else np.nan
                   for e in comp["ech_eff"].fillna(comp["echeance"])]
    comp["y"] = comp["ecart_pb"].clip(-10, 10)
    g2 = go.Figure()
    for g, coul in (("Conforme", T.VERT), ("Écart de sensibilité", "#E0A100"), ("Écart de prix", T.ROUGE)):
        s = comp[comp["statut_lib"] == g]
        if s.empty:
            continue
        symb = np.where(s["ecart_pb"] > 10, "triangle-up", np.where(s["ecart_pb"] < -10, "triangle-down", "circle"))
        g2.add_scatter(x=s["mat"], y=s["y"], mode="markers", name=g, marker=dict(color=coul, size=8, symbol=symb,
                       line=dict(width=0.5, color="white")), customdata=np.stack([s["code"], s["ecart_pb"], s["ecart_mad"]], axis=1),
                       hovertemplate="%{customdata[0]} · %{x:.2f} ans<br>%{customdata[1]:+.3f} pb · %{customdata[2]:+,.2f} MAD<extra></extra>")
    g2 = mise_en_page(g2, 300, xaxis_title="Maturité résiduelle retenue (années)", yaxis_title="Écart (pb du prix)",
                      legend=dict(orientation="h", y=1.12, x=0))
    g2.update_yaxes(range=[-11, 11])
    return entete + info, {}, cache, kpis, t_cat, T.theme_fig(g1, ui), T.theme_fig(g2, ui)


@callback(Output("cv-table", "data"), Output("cv-table", "selected_rows"), Output("cv-n", "children"),
          Input("cv-fichier", "data"), Input("cv-valo", "date"), Input("cv-courbe", "date"),
          Input("cv-tol-prix", "value"), Input("cv-tol-sensi", "value"), Input("cv-filtre", "value"), Input("maj", "data"))
def lignes(fichier, valo, courbe, tol_prix, tol_sensi, filtre, _):
    if not (fichier and valo and courbe):
        raise PreventUpdate
    cle, _ = _export(fichier)
    df, _ = C.comparer(cle, valo, courbe, tol_prix or 0, tol_sensi or 0)
    if df.empty:
        return [], [], None
    sel = {"mal": df["statut"].isin(C.MAL_PRICES), "ok": df["statut"] == "ok",
           "non": df["statut"].isin(C.NON_COMPAREES)}.get(filtre, pd.Series(True, index=df.index))
    d = df[sel].copy()
    d = d.reindex(d["ecart_mad"].abs().sort_values(ascending=False, na_position="last").index)
    d["id"] = d["code"]
    cols = [c["id"] for c in COLONNES] + ["id"]
    return d[cols].replace({np.nan: None}).to_dict("records"), [], f"{len(d)} lignes."


@callback(Output("cv-detail", "children"), Input("cv-table", "selected_row_ids"),
          State("cv-fichier", "data"), State("cv-valo", "date"), State("cv-courbe", "date"),
          State("cv-tol-prix", "value"), State("cv-tol-sensi", "value"))
def detail(ids, fichier, valo, courbe, tol_prix, tol_sensi):
    if not ids:
        return html.Div("Cochez une ligne pour voir les paramètres utilisés et l'échéancier calculé.",
                        className="info info-legere")
    cle, _ = _export(fichier)
    df, _ = C.comparer(cle, valo, courbe, tol_prix or 0, tol_sensi or 0)
    r = df[df["code"] == ids[0]].iloc[0]
    f = BT.fiche(r["code"])
    infos = [("Code", r["code"]), ("Ligne Excel", r["ligne"]), ("Catégorie", r["categorie"]),
             ("Statut", r["statut_lib"]), ("Cause probable", r["cause"] or "—")]
    if f is None:
        return html.Div(className="carte", children=[etiquette("Détail", r["code"]),
                                                    html.Div("Code absent de base_titre.xlsx.", className="alerte")])
    params = [("Type", r["type_lib"]), ("Émetteur", f["emetteur"]), ("Nominal", mad(f["nominal"])),
              ("Taux facial", f"{f['tf']:.3f} %"), ("Base du coupon", r["base_coupon"]), ("Spread", f"{f['prime']:g} pb"),
              ("Émission", fdate(f["em"])), ("Jouissance", fdate(f["jouis"])), ("Échéance", fdate(f["ech"])),
              ("Fréquence", f["freq"]), ("Prochaine révision", fdate(f["revisions"][0]) if f["revisions"] else "—"),
              ("Délai de grâce", f"{f['grace']:g} an(s)" if f.get("grace") else "—")]
    cmp_rows = [
        ("Prix plein (MAD)", mad(r["valo_fichier"]), mad(r["valo_calcul"]), mad(r["ecart_mad"], True)),
        ("Sensibilité", f"{r['sensi_fichier']:.6f}" if pd.notna(r["sensi_fichier"]) else "—",
         f"{r['sensi_calcul']:.6f}" if pd.notna(r["sensi_calcul"]) else "—",
         f"{r['ecart_sensi']:+.6f}" if pd.notna(r["ecart_sensi"]) else "—"),
        ("Duration", f"{r['duration_fichier']:.6f}" if pd.notna(r["duration_fichier"]) else "—",
         f"{r['duration_calcul']:.6f}" if pd.notna(r["duration_calcul"]) else "—",
         f"{r['ecart_duration']:+.6f}" if pd.notna(r["ecart_duration"]) else "—"),
    ]
    enfants = [
        html.Div(className="carte-tete", children=[etiquette("Détail du calcul", f"{r['code']} — {r['description']}")]),
        html.Div(className="swap-grille", children=[
            html.Div(className="swap-ligne", children=[
                html.Div("Paramètres (base titre)", className="swap-titre"),
                html.Div(className="info-grille info-grille-3", children=[
                    html.Div(className="info-ligne", children=[html.Span(a, className="info-lib"), html.Span(b, className="info-val")])
                    for a, b in params + [("Taux de valo. retenu", f"{r['taux']:.3f} %" if pd.notna(r["taux"]) else "—"),
                                          ("Capital restant dû", mad(r["crd"]))]]),
            ]),
            html.Div(className="swap-ligne", children=[
                html.Div("Comparaison", className="swap-titre"),
                html.Table(className="tbl dense", children=[
                    html.Thead(html.Tr([html.Th(""), html.Th("Fichier"), html.Th("Calcul"), html.Th("Écart")])),
                    html.Tbody([html.Tr([html.Td(a, className="premiere"), html.Td(b), html.Td(c), html.Td(d)])
                                for a, b, c, d in cmp_rows])]),
                html.Div(className="info-grille", children=[
                    html.Div(className="info-ligne large", children=[html.Span(a, className="info-lib"),
                                                                    html.Span(b, className="info-val info-libre")])
                    for a, b in infos[3:]]),
            ]),
        ]),
    ]
    if r["statut"] in ("ok", "prix", "sensi"):
        courbe_df, _ = SW.courbe_en_base(courbe)
        v = PR.valoriser(SW._parametres(f, date.fromisoformat(valo)), courbe_df)
        tab = v["tab"].set_index("date_de_reglement")
        spec = [{"col": "maturite_residuelle_en_annee", "lib": "Maturité (ans)", "genre": "n3", "cls": "ref"},
                {"col": "taux_de_valorisation", "lib": "Taux de valo.", "genre": "pct", "cls": "ref"},
                {"col": "flux", "lib": "Flux (MAD)", "genre": "mad", "cls": "j"},
                {"col": "flux_actualise", "lib": "Flux actualisé", "genre": "mad", "cls": "j"}]
        enfants.append(html.Div(className="tableau-defile", style={"marginTop": "1rem"}, children=[
            html.Div(f"Échéancier calculé · coupon couru {mad(v['cc'])} MAD"
                     + (" · révisable valorisé au taux monétaire jusqu'à la révision" if v["rev_monetaire"] else ""),
                     className="note"),
            table(tab, spec, premiere="Date", dense="dense")]))
    return html.Div(className="carte", children=enfants)


@callback(Output("cv-dl", "data"), Output({"type": "sig-toast", "id": "controle-toast-1"}, "data"),
          Input("cv-export", "n_clicks"), State("cv-fichier", "data"), State("cv-valo", "date"), State("cv-courbe", "date"),
          State("cv-tol-prix", "value"), State("cv-tol-sensi", "value"), prevent_initial_call=True)
def exporter(n, fichier, valo, courbe, tol_prix, tol_sensi):
    if not n:
        raise PreventUpdate
    if not fichier:
        return no_update, toast("Déposez d'abord un export.", "erreur")
    cle, _ = _export(fichier)
    df, _ = C.comparer(cle, valo, courbe, tol_prix or 0, tol_sensi or 0)
    R = C.resume(df)
    resume = pd.DataFrame([
        ("Fichier", fichier["nom"]), ("Date de la courbe", fdate(courbe)), ("Date de valorisation", fdate(valo)),
        ("Tolérance prix (MAD)", tol_prix), ("Tolérance sensi", tol_sensi), ("Lignes", R["n"]),
        ("Comparées", R["comparees"]), ("Conformes", R["ok"]), ("Écarts de prix", R["prix"]),
        ("Écarts de sensibilité", R["sensi"]), ("Non comparées", R["non_comparees"]),
        ("Écart médian (pb)", R["ecart_med_pb"]), ("Somme des écarts absolus (MAD par titre)", R["ecart_abs_total"]),
    ], columns=["Indicateur", "Valeur"]).astype({"Valeur": str})
    noms = {"ligne": "Ligne Excel", "code": "Code", "description": "Description", "classe": "Classe",
            "categorie": "Catégorie", "type_lib": "Type", "emetteur": "Émetteur", "base_coupon": "Base coupon",
            "tf": "Taux facial", "prime": "Spread (pb)", "valo_fichier": "Valo fichier", "valo_calcul": "Valo calculée",
            "ecart_mad": "Écart (MAD)", "ecart_pb": "Écart (pb)", "sensi_fichier": "Sensi fichier",
            "sensi_calcul": "Sensi calculée", "ecart_sensi": "Écart sensi", "duration_fichier": "Duration fichier",
            "duration_calcul": "Duration calculée", "ecart_duration": "Écart duration", "taux": "Taux de valo",
            "crd": "Capital restant dû", "statut_lib": "Statut", "cause": "Cause probable"}
    lignes_df = df[[c for c in noms if c in df.columns]].rename(columns=noms)
    feuilles = {"Résumé": resume, "Par catégorie": C.par_categorie(df).reset_index(names="Catégorie"),
                "Mal pricées": lignes_df[df["statut"].isin(C.MAL_PRICES).to_numpy()],
                "Non comparées": lignes_df[df["statut"].isin(C.NON_COMPAREES).to_numpy()], "Toutes": lignes_df}
    return dcc.send_bytes(db.exporter_classeur(feuilles), f"controle_valo_{(valo or '').replace('-', '')}.xlsx"), \
        toast("Contrôle exporté en Excel.")


@callback(Output({"type": "sig-maj", "id": "controle-maj-3"}, "data"), Output({"type": "sig-toast", "id": "controle-toast-2"}, "data"),
          Input("cv-bkam", "n_clicks"), State("cv-courbe", "date"), State("maj", "data"), prevent_initial_call=True)
def bkam(n, d, maj):
    if not n or not d:
        raise PreventUpdate
    try:
        try:
            import COURBE as courbe
        except ImportError:
            import Courbe as courbe
        dj = date.fromisoformat(d)
        brute = courbe.get_bkam_taux_reference(dj)
        if len(brute) < 2:
            return no_update, toast(f"BKAM n'a pas publié de courbe pour le {fdate(d)}.", "erreur")
        tab = courbe.tableau_tenors(brute, dj, formate=False)
        with db.connexion() as conn:
            db.enregistrer_courbe(conn, dj, dict(zip(tab["Ténor"], tab.iloc[:, 2])), brute, source="bkam")
        return time.time(), toast(f"Courbe du {fdate(d)} téléchargée et historisée.")
    except Exception as e:                                                   # noqa: BLE001
        return no_update, toast(f"Échec du téléchargement BKAM : {e}", "erreur")
