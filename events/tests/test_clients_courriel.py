"""Transferts et réponses tels que les écrivent les clients de messagerie courants.

Chaque client a sa façon d'écrire le bloc d'en-tête d'un transfert et la ligne qui
annonce une citation : Outlook (bureau, web, Hotmail, mobile, Mac), Gmail (web et
Android), Yahoo, Apple Mail (Mac et iPhone), Thunderbird, Proton et Samsung Email,
en français, anglais, néerlandais et allemand. Les blocs ci-dessous reproduisent leur
texte brut ; un même courriel d'ACME Bat, envoyé le 25/08/2026 à 14:31, y est transféré
ou cité.
"""

import datetime

from django.test import SimpleTestCase

from events.courriels import BRUXELLES, analyser, date_en_toutes_lettres
from events.liens import citees
from events.tests.test_courriels import courriel

EV = "ACME Bat <contact@acme-bat.example>"
MOI = "Jean <jdupont@exemple.be>"
ENVOI = datetime.datetime(2026, 8, 25, 14, 31, tzinfo=BRUXELLES)

#: (objet du transfert, bloc) : l'objet n'annonce pas toujours le transfert, la ligne
#: au-dessus du bloc s'en charge alors.
TRANSFERTS = {
    "outlook bureau fr": ("TR: Réponse 2e acompte",
                          "________________________________\nDe : %s\nEnvoyé : mardi 25 août 2026 14:31\n"
                          "À : %s\nObjet : Réponse 2e acompte" % (EV, MOI)),
    "outlook.com / hotmail en": ("FW: Réponse 2e acompte",
                                 "________________________________\nFrom: %s\nSent: Tuesday, August 25, 2026 2:31 PM\n"
                                 "To: %s\nSubject: Réponse 2e acompte" % (EV, MOI)),
    "outlook mobile": ("Fw: Réponse 2e acompte",
                       "From: %s\nSent: Tuesday, August 25, 2026 2:31:07 PM\nTo: %s\n"
                       "Subject: Réponse 2e acompte" % (EV, MOI)),
    "outlook mac fr": ("TR: Réponse 2e acompte",
                       "De : %s\nDate : mardi 25 août 2026 à 14:31\nÀ : %s\nObjet : Réponse 2e acompte" % (EV, MOI)),
    "outlook de": ("WG: Réponse 2e acompte",
                   "Von: %s\nGesendet: Dienstag, 25. August 2026 14:31\nAn: %s\n"
                   "Betreff: Réponse 2e acompte" % (EV, MOI)),
    "gmail en": ("Réponse 2e acompte",
                 "---------- Forwarded message ---------\nFrom: %s\nDate: Tue, Aug 25, 2026 at 2:31 PM\n"
                 "Subject: Réponse 2e acompte\nTo: %s" % (EV, MOI)),
    "gmail fr": ("Réponse 2e acompte",
                 "---------- Message transféré ---------\nDe : %s\nDate: mar. 25 août 2026 à 14:31\n"
                 "Subject: Réponse 2e acompte\nTo: %s" % (EV, MOI)),
    "gmail nl": ("Réponse 2e acompte",
                 "---------- Doorgestuurd bericht ---------\nVan: %s\nDate: di 25 aug 2026 om 14:31\n"
                 "Subject: Réponse 2e acompte\nTo: %s" % (EV, MOI)),
    "yahoo fr": ("Réponse 2e acompte",
                 "----- Mail transféré -----\nDe : %s\nÀ : %s\n"
                 "Envoyé : mardi 25 août 2026 à 14:31:07 UTC+2\nObjet : Réponse 2e acompte" % (EV, MOI)),
    "yahoo en": ("Réponse 2e acompte",
                 "----- Forwarded Message -----\nFrom: %s\nTo: %s\n"
                 "Sent: Tuesday, August 25, 2026, 02:31:07 PM GMT+2\nSubject: Réponse 2e acompte" % (EV, MOI)),
    "apple mail fr": ("Réponse 2e acompte",
                      "Début du message réexpédié :\n\nDe: %s\nObjet: Réponse 2e acompte\n"
                      "Date: 25 août 2026 à 14:31:07 UTC+2\nÀ: %s" % (EV, MOI)),
    "apple mail en": ("Réponse 2e acompte",
                      "Begin forwarded message:\n\nFrom: %s\nSubject: Réponse 2e acompte\n"
                      "Date: 25 August 2026 at 14:31:07 CEST\nTo: %s" % (EV, MOI)),
    "apple mail nl": ("Réponse 2e acompte",
                      "Begin doorgestuurd bericht:\n\nVan: %s\nOnderwerp: Réponse 2e acompte\n"
                      "Datum: 25 augustus 2026 om 14:31:07 CEST\nAan: %s" % (EV, MOI)),
    "thunderbird en": ("Fwd: Réponse 2e acompte",
                       "-------- Forwarded Message --------\nSubject: \tRéponse 2e acompte\n"
                       "Date: \tTue, 25 Aug 2026 14:31:07 +0200\nFrom: \t%s\nTo: \t%s" % (EV, MOI)),
    "thunderbird fr": ("Réponse 2e acompte",
                       "-------- Message transféré --------\nSujet : \tRéponse 2e acompte\n"
                       "Date : \tmar. 25 août 2026 14:31:07\nDe : \t%s\nPour : \t%s" % (EV, MOI)),
    "proton": ("Fwd: Réponse 2e acompte",
               "------- Forwarded Message -------\nFrom: %s\nDate: On Tuesday, August 25th, 2026 at 2:31 PM\n"
               "Subject: Réponse 2e acompte\nTo: %s" % (EV, MOI)),
}

#: La ligne qui annonce le courriel cité par une réponse.
CITATIONS = {
    "gmail fr": "Le mar. 25 août 2026 à 14:31, %s a écrit :" % EV,
    "gmail fr coupé en deux lignes": "Le mar. 25 août 2026 à 14:31, ACME Bat <\ncontact@acme-bat.example> a écrit :",
    "gmail en": "On Tue, Aug 25, 2026 at 2:31 PM %s wrote:" % EV,
    "gmail nl": "Op di 25 aug 2026 om 14:31 schreef %s:" % EV,
    "gmail de": "Am Di., 25. Aug. 2026 um 14:31 Uhr schrieb %s:" % EV,
    "apple mail / iphone fr": "> Le 25 août 2026 à 14:31, %s a écrit :" % EV,
    "apple mail / iphone en": "On 25 Aug 2026, at 14:31, %s wrote:" % EV,
    "yahoo fr": "Le mardi 25 août 2026 à 14:31:07 UTC+2, %s a écrit :" % EV,
    "yahoo en": "On Tuesday, August 25, 2026, 02:31:07 PM GMT+2, %s wrote:" % EV,
    "thunderbird fr": "Le 25/08/2026 à 14:31, %s a écrit :" % EV,
    "thunderbird en-us": "On 8/25/26 2:31 PM, %s wrote:" % EV,
    "thunderbird nl": "Op 25-08-2026 om 14:31 schreef %s:" % EV,
    "proton": "On Tuesday, August 25th, 2026 at 2:31 PM, %s wrote:" % EV,
    "outlook mobile": "From: %s\nSent: Tuesday, August 25, 2026 2:31:07 PM\nTo: %s\nSubject: Réponse 2e acompte" % (EV, MOI),
    "outlook fr": "De : %s\nEnvoyé : mardi 25 août 2026 14:31\nÀ : %s\nObjet : Réponse 2e acompte" % (EV, MOI),
}


class TransfertParClientTests(SimpleTestCase):
    def test_chaque_client(self):
        for client, (objet, bloc) in TRANSFERTS.items():
            with self.subTest(client=client):
                texte = "À verser au dossier.\n\n" + bloc + "\n\nBonjour,\nNous contestons."
                a = analyser(courriel(objet, texte, de=MOI).as_bytes())
                self.assertEqual(a.forme, "texte")
                self.assertEqual(a.titre, "Réponse 2e acompte")
                self.assertEqual(a.moment.replace(second=0), ENVOI)
                self.assertFalse(a.date_approchee)
                self.assertIn("contact@acme-bat.example", a.expediteur)
                self.assertEqual(a.texte, "Bonjour,\nNous contestons.")
                self.assertEqual(a.commentaire, "À verser au dossier.")

    def test_iphone_cite_le_bloc_transfere(self):
        # L'iPhone met le bloc et le texte transférés derrière des « > ».
        texte = ("À verser au dossier.\n\nDébut du message réexpédié :\n\n"
                 "> De: %s\n> Date: 25 août 2026 à 14:31:07 UTC+2\n> À: %s\n"
                 "> Objet: Réponse 2e acompte\n> \n> Bonjour,\n> Nous contestons.\n" % (EV, MOI))
        a = analyser(courriel("Réponse 2e acompte", texte, de=MOI).as_bytes())
        self.assertEqual(a.forme, "texte")
        self.assertEqual(a.moment.replace(second=0), ENVOI)
        self.assertIn("contact@acme-bat.example", a.expediteur)
        self.assertEqual(a.texte, "Bonjour,\nNous contestons.")
        self.assertEqual(a.commentaire, "À verser au dossier.")

    def test_une_reponse_reste_une_reponse(self):
        # Sans objet ni ligne de transfert, le bloc cité est celui d'une réponse.
        for client in ("outlook fr", "outlook mobile"):
            with self.subTest(client=client):
                texte = "Je conteste.\n\n" + CITATIONS[client] + "\n\nBonjour,\nNous contestons."
                a = analyser(courriel("RE: Réponse 2e acompte", texte, de=MOI).as_bytes())
                self.assertEqual(a.forme, "direct")
                self.assertIn("jdupont@exemple.be", a.expediteur)


class CitationParClientTests(SimpleTestCase):
    def test_chaque_client(self):
        for client, ligne in CITATIONS.items():
            with self.subTest(client=client):
                texte = "Je conteste formellement.\n\nJean\n\n" + ligne + "\n\n> Bonjour,\n> Nous contestons.\n"
                a = analyser(courriel("RE: Réponse 2e acompte", texte, de=MOI).as_bytes())
                trouvees = citees(a)
                self.assertEqual(len(trouvees), 1, trouvees)
                cite = trouvees[0]
                self.assertEqual(cite.empreinte.moment.replace(second=0), ENVOI)
                self.assertEqual(cite.empreinte.expediteur[0], "contact@acme-bat.example")
                self.assertNotRegex(cite.de.lower(), r"utc|gmt|cest|\n")


class DatesEcritesTests(SimpleTestCase):
    def test_formes_de_dates(self):
        cas = {
            "mercredi 1er octobre 2026 à 9:05": datetime.datetime(2026, 10, 1, 9, 5),
            "October 1st, 2026 at 9:05 AM": datetime.datetime(2026, 10, 1, 9, 5),
            "2026-10-27 11:25": datetime.datetime(2026, 10, 27, 11, 25),
            "8/25/26 2:31 PM": datetime.datetime(2026, 8, 25, 14, 31),
            "25/08/2026 14:31": datetime.datetime(2026, 8, 25, 14, 31),
            "05/08/2026 14:31": datetime.datetime(2026, 8, 5, 14, 31),
            "Tue, 25 Aug 2026 14:31:07 +0200": datetime.datetime(2026, 8, 25, 14, 31, 7),
            "Di., 25. Aug. 2026 um 14:31 Uhr": datetime.datetime(2026, 8, 25, 14, 31),
            "3. März 2026 08:00": datetime.datetime(2026, 3, 3, 8, 0),
        }
        for ecrite, attendue in cas.items():
            with self.subTest(date=ecrite):
                self.assertEqual(date_en_toutes_lettres(ecrite), attendue)
