"""Portefeuille — FCM : composition depuis Manar (portefeuille = 'FCM'), revalorisation et analyses."""

import dash

import vue_portefeuille as V

dash.register_page(__name__, path="/portefeuille/fcm", name="FCM", order=1, section="portefeuille")

layout = V.construire("FCM")
