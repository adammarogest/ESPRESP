"""Petits composants visuels réutilisés par les fenêtres."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

import theme as T

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def toast(message: str, genre: str = "ok") -> dict:
    """
    Notification : signal daté écrit par une page dans son propre store ({"type": "sig-toast", ...}) ;
    le collecteur de l'application affiche le plus récent dans la zone de notification.
    (Une page n'écrit jamais directement dans un composant commun : avec les pages figées, Dash
    relancerait ces callbacks à chaque nouvelle page montée.)
    """
    return {"t": time.time(), "message": message, "genre": genre}


def rendu_toast(signal: dict) -> html.Div:
    """Notification éphémère (l'id unique relance l'animation à chaque message)."""
    return html.Div(signal["message"], className=f"toast toast-{signal.get('genre', 'ok')}",
                    id=f"toast-{int(signal['t'] * 1e6)}")


def signaux(ids: list[str]) -> html.Div:
    """Stores de signaux d'une page : « <page>-<canal>-<n> », canal = toast, maj ou treso."""
    return html.Div([dcc.Store(id={"type": f"sig-{i.rsplit('-', 2)[-2]}", "id": i}) for i in ids], style={"display": "none"})


def etiquette(fort: str, doux: str | None = None) -> html.Div:
    enfants = [html.Span(className="etiq-barre"), html.Span(fort, className="fort")]
    if doux:
        enfants += [html.Span(className="sep"), html.Span(doux, className="doux")]
    return html.Div(enfants, className="etiquette")


def fdate(d, fmt: str = "%d/%m/%Y") -> str:
    return "—" if d is None or pd.isna(d) else pd.Timestamp(d).strftime(fmt)


def jour(d) -> str:
    return "" if d is None or pd.isna(d) else JOURS[pd.Timestamp(d).weekday()]


# ---------------------------------------------------------------------------
# Mise en forme (tableaux, cartes, graphiques)
# ---------------------------------------------------------------------------

def fmt(v, genre: str) -> str:
    if v is None or (isinstance(v, (float, np.floating)) and np.isnan(v)):
        return "—"
    if genre == "txt" or isinstance(v, str):
        return str(v)
    formats = {
        "pct": "{:.3f}%", "pct2": "{:.2f}%", "pb": "{:+.1f}", "pb1": "{:.1f}",
        "n2": "{:.2f}", "n3": "{:.3f}", "p": "{:.3f}",
    }
    if genre == "part":
        return f"{v * 100:.1f}%"
    if genre == "int":
        return f"{int(v):,}".replace(",", " ")
    if genre in ("mad", "mad+"):
        return (f"{v:+,.2f}" if genre == "mad+" else f"{v:,.2f}").replace(",", " ")
    return formats[genre].format(v)


def cls_signe(v) -> str:
    if v is None or pd.isna(v):
        return "stable"
    return "hausse" if v > 0.05 else "baisse" if v < -0.05 else "stable"


def cls_p(v) -> str:
    return "sig" if v is not None and pd.notna(v) and v < 0.05 else "ref"


def table(df: pd.DataFrame, spec: list[dict], groupes: list[tuple[str, int]] | None = None,
          premiere: str = "Ténor", compact: bool = False, styles=None, dense: str = "") -> html.Table:
    """
    spec : [{"col", "lib", "genre", "cls" (str ou fonction valeur -> classe)}]
    groupes : [(libellé, nb colonnes)] pour une ligne d'en-tête groupée.
    styles : fonction (index, col, valeur) -> dict de style CSS pour la cellule.
    """
    if groupes:
        tete = [html.Tr([html.Th(premiere, rowSpan=2, className="gauche")] +
                        [html.Th(g, colSpan=n, className="groupe") for g, n in groupes]),
                html.Tr([html.Th(c["lib"]) for c in spec])]
    else:
        tete = [html.Tr([html.Th(premiere, className="gauche")] + [html.Th(c["lib"]) for c in spec])]
    lignes = []
    for idx, r in df.iterrows():
        cellules = [html.Td(fmt(idx, "txt") if not isinstance(idx, pd.Timestamp) else fdate(idx),
                            className="tenor gauche")]
        for c in spec:
            v = r[c["col"]]
            cls = c.get("cls", "")
            cls = cls(v) if callable(cls) else cls
            style = styles(idx, c["col"], v) if styles else None
            cellules.append(html.Td(fmt(v, c.get("genre", "n2")), className=cls, style=style))
        lignes.append(html.Tr(cellules))
    return html.Table(className=("tbl " + ("compact " if compact else "") + dense).strip(),
                      children=[html.Thead(tete), html.Tbody(lignes)])


def mise_en_page(fig: go.Figure, hauteur: int = 380, **kwargs) -> go.Figure:
    fig.update_layout(**T.PLOTLY_LAYOUT, height=hauteur)
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor="rgba(14,77,92,0.18)", tickfont=dict(color=T.GRIS))
    fig.update_yaxes(gridcolor="rgba(14,77,92,0.07)", zeroline=False, tickfont=dict(color=T.GRIS))
    fig.update_layout(**kwargs)
    return fig


def graphe(id_: str, hauteur: int = 380) -> dcc.Graph:
    return dcc.Graph(id=id_, config={"displayModeBar": False}, style={"height": f"{hauteur}px"})


def carte(titre: str, sous_titre: str | None, *enfants, outils=None, classe: str = "") -> html.Div:
    tete = [etiquette(titre, sous_titre)] + ([outils] if outils is not None else [])
    return html.Div(className=f"carte {classe}", children=[html.Div(tete, className="carte-tete"), *enfants])


def segment(id_: str, options: list, valeur) -> dcc.RadioItems:
    return dcc.RadioItems(id=id_, value=valeur, inline=True, className="segment",
                          options=[{"label": o if not isinstance(o, tuple) else o[0],
                                    "value": o if not isinstance(o, tuple) else o[1]} for o in options])


def kpi(lib: str, valeur: str, sous: str = "", couleur: str | None = None) -> html.Div:
    return html.Div(className="kpi", children=[
        html.Div(lib, className="kpi-lib", style={"color": couleur} if couleur else None),
        html.Div(valeur, className="kpi-val"),
        html.Div(sous, className="kpi-sous"),
    ])


def note(*lignes) -> html.Div:
    return html.Div(className="note-bloc", children=[html.P(l) for l in lignes])


