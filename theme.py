"""Charte Espresso des graphiques (partagée par toutes les fenêtres) : jour « Porcelaine », nuit « Ristretto »."""

from __future__ import annotations

import json
import re

import plotly.io as pio

# Charte Espresso (jour « Porcelaine »)
NAVY = "#0E4D5C"          # pétrole : série principale
NAVY_FONCE = "#0A3640"
ENCRE = "#1C1714"         # torréfié
GRIS = "#66625D"
GRIS_CLAIR = "#A39D96"
TRAIT = "rgba(14, 77, 92, 0.10)"
MENTHE = "#9BCBB0"
VERT = "#1E7A4F"
ROUGE = "#C1272D"
CIEL = "#7FA9B3"          # pétrole pâle : série secondaire
CREMA = "#B07A3B"         # accent

POLICE = "Manrope, 'Segoe UI', sans-serif"
POLICE_NUIT = POLICE

PLOTLY_LAYOUT = dict(
    font=dict(family=POLICE, color=ENCRE, size=11),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=8, r=8, t=36, b=8),
    hovermode="x unified",
    hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E3DFD9", font=dict(family=POLICE, color=ENCRE)),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=11, color=GRIS), bgcolor="rgba(0,0,0,0)"),
)

# Anciennes couleurs encore écrites dans certaines fenêtres -> charte Espresso de jour
_JOUR = {
    "rgba(11,74,139,": "rgba(14,77,92,", "rgba(11, 74, 139,": "rgba(14, 77, 92,",
    "rgba(143,174,208,": "rgba(127,169,179,", "rgba(226,0,26,": "rgba(193,39,45,",
    "#0B4A8B": NAVY, "#062F5C": NAVY_FONCE, "#3F7CC0": "#3F8794", "#8FAED0": CIEL, "#0E7A3E": VERT,
    "#5DBB85": "#5FA882", "#7DD3A0": MENTHE, "#E2001A": ROUGE, "#F07A86": "#E38E84", "#5E6A7A": GRIS,
    "#9AA5B4": GRIS_CLAIR, "#0F1B2D": ENCRE, "#F4F6FA": "#F3F4F2", "#DCE3EC": "#E3DFD9",
    "#C98F00": CREMA, "#E0A100": "#C9974F",
}

# Nuit « Ristretto » : charte de jour -> brun espresso, mousse de lait, crema dorée
# (les rgba les plus précis passent avant les règles générales)
_NUIT = {
    "rgba(14,77,92,0.07)": "rgba(236,227,215,0.06)", "rgba(14,77,92,0.12)": "rgba(236,227,215,0.12)",
    "rgba(14,77,92,0.18)": "rgba(236,227,215,0.16)", "rgba(14,77,92,0.25)": "rgba(236,227,215,0.20)",
    "rgba(14, 77, 92, 0.10)": "rgba(236, 227, 215, 0.10)",
    "rgba(14,77,92,": "rgba(217,162,95,", "rgba(127,169,179,0.28)": "rgba(111,177,188,0.18)",
    "rgba(127,169,179,": "rgba(111,177,188,", "rgba(193,39,45,": "rgba(240,106,95,",
    NAVY: "#D9A25F", NAVY_FONCE: "#E8B878", "#3F8794": "#E9C48F", CIEL: "#6FB1BC", VERT: "#5FC08A",
    "#5FA882": "#86D3A6", MENTHE: "#B9E6CB", ROUGE: "#F06A5F", "#E38E84": "#F59A8F", GRIS: "#A0958A",
    GRIS_CLAIR: "#6F665E", ENCRE: "#ECE3D7", "#F3F4F2": "#1A1512", "#E3DFD9": "#3A2F28",
    "#FFFFFF": "#1D1714", CREMA: "#E2C08F", "#C9974F": "#EBCB98",
}


def _remplacer(texte: str, table: dict) -> str:
    """Remplacement en une seule passe (pas de remplacement en chaîne), clés les plus longues d'abord."""
    motif = re.compile("|".join(re.escape(k) for k in sorted(table, key=len, reverse=True)), re.IGNORECASE)
    index = {k.upper(): v for k, v in table.items()}
    return motif.sub(lambda m: index[m.group(0).upper()], texte)


def est_nuit(ui) -> bool:
    return bool(ui and ui.get("nuit"))


def theme_fig(fig, ui):
    """Applique la charte Espresso : jour « Porcelaine » ou nuit « Ristretto »."""
    texte = _remplacer(pio.to_json(fig), _JOUR)
    nuit = est_nuit(ui)
    if nuit:
        texte = _remplacer(texte, _NUIT)
    d = json.loads(texte)
    lay = d.setdefault("layout", {})
    lay.setdefault("font", {}).update(family=POLICE)
    if nuit:
        lay.setdefault("hoverlabel", {}).update(bgcolor="#1D1714", bordercolor="#D9A25F")
        lay["hoverlabel"].setdefault("font", {}).update(family=POLICE, color="#ECE3D7")
    return d
