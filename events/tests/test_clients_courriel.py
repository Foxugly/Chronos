"""Transferts et réponses tels que les écrivent les clients de messagerie courants.

Chaque client a sa façon d'écrire le bloc d'en-tête d'un transfert et la ligne qui
annonce une citation : Outlook (bureau, web, Hotmail, mobile, Mac), Gmail (web et
Android), Yahoo, Apple Mail (Mac et iPhone), Thunderbird, Proton et Samsung Email,
en français, anglais, néerlandais et allemand. Les blocs ci-dessous reproduisent leur
texte brut ; un même courriel d'ACME Bat, envoyé le 25/08/2026 à 14:31, y est transféré
ou cité.
"""

import datetime
from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from events.courriels import BRUXELLES, analyser, date_en_toutes_lettres
from events.import_courriels import importer
from events.journal import consigner
from events.liens import citees
from events.models import Event, Journal
from events.tests.factories import make_dossier, make_user
from events.tests.test_courriels import ALIAS, MediaMixin, SansSauvegardeMixin, courriel

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


SIGNATURE_HTML = ("<p>Les\n informations contenues dans ce message\n sont de nature confidentielle.</p>")
REPONSE = ("Maître,\n\nTout va bien.\n\nJ'accepte la conciliation et la désignation d'un expert.\n\n"
           "En vous remerciant,\n\nJean")
CITE_SAMSUNG_APLATI = ("-------- Message d'origine --------De : %s Date : 27/10/26  11:25  (GMT+01:00) À : %s "
                       "Objet : Dossier ACME BAT Cher Monsieur,\xa0J'espère que vous allez bien.\xa0J'ai préparé une "
                       "lettre pour ACME BAT.Pouvez-vous en prendre connaissance ?\xa0En vous remerciant,\xa0"
                       "CdtAnne DuboisAvocate au Barreau de Bruxelles" % (EV, MOI))
CITE_SAMSUNG_HTML = ("<div>-------- Message d'origine --------</div><div>De : ACME Bat &lt;contact@acme-bat.example&gt; </div>"
                     "<div>Date : 27/10/26  11:25  (GMT+01:00) </div><div>À : %s </div>"
                     "<div>Objet : Dossier ACME BAT </div><div><br></div><div>Cher Monsieur,</div>"
                     "<div>J'espère que vous allez bien.</div>" % "Jean &lt;jdupont@exemple.be&gt;" + SIGNATURE_HTML)


class SuiteExchangeAplatieTests(SimpleTestCase):
    """Une réponse écrite sur Samsung, transférée par Outlook (n° 214 d'ACMEBAT) : Exchange
    détache la suite du corps en ATT00001.txt (la réponse, aplatie), ATT00003.htm (le
    courriel cité, lisible) et ATT00004.txt (le même, aplati)."""

    def courriel_transfere(self):
        bloc = ("________________________________\nFrom: %s\nSent: Tuesday, October 27, 2026 14:30\n"
                "To: %s\nSubject: RE: Dossier ACME BAT\n\n" % (MOI, EV))
        m = courriel("TR: RE: Dossier ACME BAT", bloc + REPONSE, de=MOI)
        m.add_attachment(" ".join(REPONSE.split()).replace(" ", "\xa0", 3), subtype="plain", filename="ATT00001.txt")
        m.add_attachment(CITE_SAMSUNG_HTML, subtype="html", filename="ATT00003.htm")
        m.add_attachment(CITE_SAMSUNG_APLATI, subtype="plain", filename="ATT00004.txt")
        return m.as_bytes()

    def test_la_suite_est_lue_dans_le_html_sans_repeter_la_reponse(self):
        a = analyser(self.courriel_transfere())
        self.assertEqual(a.texte.count("Tout va bien"), 1)
        self.assertIn("\nDe : ACME Bat <contact@acme-bat.example>\nDate : 27/10/26 11:25 (GMT+01:00)\n", a.texte)
        self.assertNotIn("Message d'origine --------De", a.texte)
        self.assertIn("Les informations contenues dans ce message sont de nature confidentielle.", a.texte)

    def test_le_courriel_cite_compte_une_fois_avec_son_objet(self):
        trouvees = citees(analyser(self.courriel_transfere()))
        self.assertEqual(len(trouvees), 1)
        self.assertEqual(trouvees[0].objet, "Dossier ACME BAT")
        self.assertEqual(trouvees[0].empreinte.moment, datetime.datetime(2026, 10, 27, 11, 25, tzinfo=BRUXELLES))


class HtmlEnTexteTests(SimpleTestCase):
    def test_les_retours_du_source_html_ne_coupent_pas_les_phrases(self):
        m = courriel("Devis", "x", html=SIGNATURE_HTML)
        m.get_body(preferencelist=("plain",)).set_content("x" * 10)  # garde la partie texte courte
        from events.courriels import corps_en_texte, lire
        texte = corps_en_texte(lire(m.as_bytes()), preference=("html",))
        self.assertEqual(texte, "Les informations contenues dans ce message sont de nature confidentielle.")

    def test_un_pre_garde_ses_retours_a_la_ligne(self):
        """Un courriel en texte brut emballé dans `<pre>` : ses lignes sont les siennes."""
        from events.courriels import corps_en_texte, lire
        html = "<div>Bonjour,</div><pre>Ligne 1\nLigne 2\n\nLigne 4</pre><div>Fin</div>"
        texte = corps_en_texte(lire(courriel("Devis", "x", html=html).as_bytes()), preference=("html",))
        self.assertEqual(texte, "Bonjour,\nLigne 1\nLigne 2\n\nLigne 4\nFin")

    def test_une_citation_commence_sur_sa_ligne(self):
        from events.courriels import corps_en_texte, lire
        html = ("<div>Merci.</div><div>Le 14 oct. 2026 à 15:13, %s a écrit :"
                "<blockquote><div>Cher Monsieur,</div></blockquote></div>" % EV.replace("<", "&lt;").replace(">", "&gt;"))
        texte = corps_en_texte(lire(courriel("RE: Devis", "x", html=html).as_bytes()), preference=("html",))
        self.assertIn("a écrit :\nCher Monsieur,", texte)

    def test_un_texte_brut_aplati_cede_la_place_au_html(self):
        # Un courriel écrit sur Samsung, envoyé directement : son texte brut tient sur une ligne.
        aplati = " ".join(REPONSE.split()) * 3
        html = "".join("<div>%s</div>" % ligne for ligne in REPONSE.split("\n"))
        a = analyser(courriel("Accord", aplati, html=html).as_bytes())
        self.assertEqual(a.forme, "direct")
        self.assertTrue(a.texte.startswith("Maître,\n\nTout va bien."))


class CommandeRelireTests(SansSauvegardeMixin, MediaMixin, TestCase):
    """`relire_courriels` refait la description d'un évènement importé avant une
    amélioration de la lecture (n° 212 et 214 d'ACMEBAT)."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()
        self.evenement = importer(self.dossier, analyser(SuiteExchangeAplatieTests().courriel_transfere()))
        # Ce que l'ancienne lecture avait écrit.
        Event.objects.filter(pk=self.evenement.pk).update(description="<p>aplati</p>")

    def lancer(self, *args):
        sortie = StringIO()
        call_command("relire_courriels", "--evenement", str(self.evenement.pk), *args, stdout=sortie)
        self.evenement.refresh_from_db()
        return sortie.getvalue()

    def test_l_essai_n_ecrit_rien(self):
        sortie = self.lancer()
        self.assertIn("Essai", sortie)
        self.assertEqual(self.evenement.description, "<p>aplati</p>")
        self.assertFalse(Journal.objects.filter(action=Journal.Action.EVENEMENT_MODIFIE).exists())

    def test_l_essai_ne_sauvegarde_pas(self):
        self.sauvegarde.reset_mock()
        self.lancer()
        self.sauvegarde.assert_not_called()

    def test_appliquer_sauvegarde_avant_d_ecrire(self):
        def avant(commande):
            self.evenement.refresh_from_db()
            self.assertEqual(self.evenement.description, "<p>aplati</p>")
        self.sauvegarde.reset_mock(side_effect=True)
        self.sauvegarde.side_effect = avant
        try:
            self.lancer("--appliquer")
        finally:
            self.sauvegarde.side_effect = None
        self.sauvegarde.assert_called_once()
        self.assertNotIn("aplati", self.evenement.description)

    def test_une_sauvegarde_ratee_arrete_tout(self):
        self.sauvegarde.side_effect = CommandError("disque plein")
        try:
            with self.assertRaisesMessage(CommandError, "disque plein"):
                self.lancer("--appliquer")
        finally:
            self.sauvegarde.side_effect = None
        self.evenement.refresh_from_db()
        self.assertEqual(self.evenement.description, "<p>aplati</p>")
        self.assertFalse(Journal.objects.filter(action=Journal.Action.EVENEMENT_MODIFIE).exists())

    def test_appliquer_refait_la_description_et_la_journalise(self):
        version = self.evenement.version
        self.lancer("--appliquer")
        self.assertIn("Tout va bien.", self.evenement.description)
        self.assertNotIn("aplati", self.evenement.description)
        self.assertEqual(self.evenement.version, version + 1)
        entree = Journal.objects.get(action=Journal.Action.EVENEMENT_MODIFIE, evenement=self.evenement)
        self.assertEqual(entree.details["changements"]["description"][0], "<p>aplati</p>")
        self.assertIsNone(entree.auteur)

    def test_une_description_retouchee_a_la_main_est_laissee(self):
        consigner(self.dossier, Journal.Action.EVENEMENT_MODIFIE, request=SimpleNamespace(user=self.dossier.owner),
                  evenement=self.evenement, details={"changements": {"description": ["a", "b"]}})
        sortie = self.lancer("--appliquer")
        self.assertIn("modifiée à la main", sortie)
        self.assertEqual(self.evenement.description, "<p>aplati</p>")


class TexteSansCitationsTests(SimpleTestCase):
    REPONSE = "Je conteste formellement.\n\nJean"

    def test_chaque_forme_de_citation(self):
        from events.liens import texte_sans_citations
        cites = {
            **{client: ligne for client, ligne in CITATIONS.items()},
            "outlook avec séparateur": "________________________________\n" + CITATIONS["outlook fr"],
            "outlook original message": "-----Original Message-----\n" + CITATIONS["outlook mobile"],
            "samsung aplati": "-------- Message d'origine --------De : %s Date : 27/10/26  11:25  (GMT+01:00) "
                              "À : %s Objet : Devis Bonjour" % (EV, MOI),
        }
        for client, citation in cites.items():
            with self.subTest(client=client):
                texte = self.REPONSE + "\n\n" + citation + "\n\n> Bonjour,\n> Nous contestons.\n"
                self.assertEqual(texte_sans_citations(texte), self.REPONSE)

    def test_sans_citation_ou_rien_que_la_citation_le_texte_reste_entier(self):
        from events.liens import texte_sans_citations
        self.assertEqual(texte_sans_citations(self.REPONSE), self.REPONSE)
        seule = CITATIONS["gmail fr"] + "\n> Bonjour"
        self.assertEqual(texte_sans_citations(seule), seule)


class DescriptionSansCitationTests(SansSauvegardeMixin, MediaMixin, TestCase):
    """La description d'une réponse s'arrête au courriel cité quand celui-ci est dans le
    dossier ; sinon elle le garde, seule trace du courriel manquant."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")

    def original(self):
        return courriel("Réponse 2e acompte", "Bonjour,\nNous contestons.", de=EV, a=MOI,
                        recu="Tue, 25 Aug 2026 12:33:00 +0000", message_id="<orig@acme-bat.example>").as_bytes()

    def reponse(self):
        texte = "Je conteste formellement.\n\nJean\n\n" + CITATIONS["outlook fr"] + "\n\nBonjour,\nNous contestons."
        return courriel("RE: Réponse 2e acompte", texte, de=MOI, a=EV, recu="Wed, 26 Aug 2026 08:00:00 +0000",
                        message_id="<rep@exemple.be>").as_bytes()

    def test_courriel_cite_present(self):
        importer(self.dossier, analyser(self.original()))
        rep = importer(self.dossier, analyser(self.reponse()))
        self.assertIn("Je conteste formellement.", rep.description)
        self.assertNotIn("Nous contestons", rep.description)
        md = next(d for d in rep.documents.all() if d.file.name.endswith(".md"))
        self.assertIn("Nous contestons", md.file.open("rb").read().decode("utf-8"))

    def test_courriel_cite_absent(self):
        rep = importer(self.dossier, analyser(self.reponse()))
        self.assertIn("Nous contestons", rep.description)

    def test_la_commande_coupe_une_reponse_dont_le_cite_est_arrive_depuis(self):
        rep = importer(self.dossier, analyser(self.reponse()))
        importer(self.dossier, analyser(self.original()))
        sortie = StringIO()
        call_command("relire_courriels", "--dossier", str(self.dossier.pk), "--appliquer", stdout=sortie)
        rep.refresh_from_db()
        self.assertNotIn("Nous contestons", rep.description)
        self.assertIn("Je conteste formellement.", rep.description)


class TransfertSamsungEnteteDUneLigneTests(SimpleTestCase):
    """Un transfert Samsung dont seul l'en-tête tient sur une ligne : le reste du texte
    a ses retours à la ligne. Le premier bloc lisible était celui du courriel cité plus
    bas — la réponse de Claire Martin (01/09 14:21) était lue comme le courriel du 01/09
    10:27 qu'elle cite, et rangée comme « déjà importé »."""

    def test_le_courriel_transfere_est_celui_de_l_en_tete_samsung(self):
        renov = "Claire Martin <cmartin@renov.example>"
        texte = ("Jean\n-------- Message d'origine --------De : %s Date : 1/09/26  14:21  (GMT+01:00) "
                 "À : %s Objet : RE: étanchéité sdb \n\nBonjour Monsieur,\n\nMerci pour l'info.\n\n"
                 "De : %s\nEnvoyé : mardi 1 septembre 2026 10:27\nÀ : Info <info@renov.example>\n"
                 "Objet : étanchéité sdb\n\nBonjour,\nVotre site est down.\n" % (renov, MOI, MOI))
        html = ("<div>Jean</div><div>-------- Message d'origine --------</div>"
                "<div>De : Claire Martin &lt;cmartin@renov.example&gt; </div>"
                "<div>Date : 1/09/26  14:21  (GMT+01:00) </div><div>À : %s </div>"
                "<div>Objet : RE: étanchéité sdb </div><div><br></div><div>Bonjour Monsieur,</div>"
                "<div>Merci pour l'info.</div><div><br></div>"
                "<div>De : %s<br>Envoyé : mardi 1 septembre 2026 10:27<br>À : Info &lt;info@renov.example&gt;<br>"
                "Objet : étanchéité sdb</div><div><br></div><div>Bonjour,</div><div>Votre site est down.</div>"
                % ("Jean &lt;jdupont@exemple.be&gt;", "Jean &lt;jdupont@exemple.be&gt;"))
        a = analyser(courriel("FW: RE: étanchéité sdb", texte, de=MOI, html=html).as_bytes())
        self.assertEqual(a.forme, "texte")
        self.assertIn("cmartin@renov.example", a.expediteur)
        self.assertEqual(a.moment, datetime.datetime(2026, 9, 1, 14, 21, tzinfo=BRUXELLES))
        self.assertTrue(a.texte.startswith("Bonjour Monsieur,"))
        self.assertEqual(citees(a)[0].empreinte.moment, datetime.datetime(2026, 9, 1, 10, 27, tzinfo=BRUXELLES))


class HeuresEcritesALaMinuteTests(SimpleTestCase):
    """Deux heures d'envoi écrites dans le texte se comparent à la minute ; une heure de
    réception suit l'envoi de 20 minutes au plus. Paul Durand a écrit deux fois au même
    fil le 03/06, à 09:13 et 09:27 : le second passait pour le premier."""

    def setUp(self):
        from events.liens import ENVOI, RECEPTION, empreinte
        self.e = lambda h, m, nature, s=0: empreinte(
            "Paul Durand <info@mediation.example>", datetime.datetime(2026, 6, 3, h, m, s, tzinfo=BRUXELLES),
            "Re: Devis travaux", nature)
        self.ENVOI, self.RECEPTION = ENVOI, RECEPTION

    def test_deux_envois_a_quatorze_minutes_sont_deux_courriels(self):
        self.assertFalse(self.e(9, 13, self.ENVOI).correspond(self.e(9, 27, self.ENVOI)))

    def test_le_meme_envoi_a_la_seconde_pres(self):
        self.assertTrue(self.e(9, 27, self.ENVOI, 59).correspond(self.e(9, 28, self.ENVOI)))

    def test_une_reception_suit_l_envoi(self):
        self.assertTrue(self.e(9, 27, self.ENVOI).correspond(self.e(9, 40, self.RECEPTION)))
        self.assertTrue(self.e(9, 40, self.RECEPTION).correspond(self.e(9, 27, self.ENVOI)))
        self.assertFalse(self.e(9, 27, self.ENVOI).correspond(self.e(9, 13, self.RECEPTION)))

    def test_sans_nature_vingt_minutes_comme_avant(self):
        self.assertTrue(self.e(9, 13, None).correspond(self.e(9, 27, self.ENVOI)))
