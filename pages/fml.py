"""Portefeuille — FML : composition depuis Manar (portefeuille = 'FML'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fml", name="FML", order=2, section="portefeuille")

layout = V.construire("FML")
