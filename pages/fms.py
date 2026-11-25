"""Portefeuille — FMS : composition depuis Manar (portefeuille = 'FMS'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fms", name="FMS", order=4, section="portefeuille")

layout = V.construire("FMS")
