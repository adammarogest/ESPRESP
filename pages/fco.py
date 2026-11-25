"""Portefeuille — FCO : composition depuis Manar (portefeuille = 'FCO'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fco", name="FCO", order=5, section="portefeuille")

layout = V.construire("FCO")
