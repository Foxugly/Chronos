"""La description mise en forme : tirée du HTML du courriel quand on l'y retrouve."""

from django.test import SimpleTestCase, TestCase

from events.courriels import analyser, analyser_en_couches
from events.import_courriels import description_de, importer
from events.mise_en_forme import Ligne, en_html, lignes_du_html, lignes_du_texte, sans_citations
from events.tests.factories import make_dossier, make_user
from events.tests.test_courriels import ALIAS, MediaMixin, courriel

TEXTE = "Bonjour,\n\nLe devis est refusé.\n\nVoir https://exemple.be/devis\n\nJean"
HTML = ("<html><head><style>p{color:red}</style></head><body>"
        "<p>Bonjour,</p><p>Le devis est <b>refusé</b>.</p>"
        "<p>Voir <a href=\"https://exemple.be/devis\" style=\"x\">le devis</a></p>"
        "<p onclick=\"x()\">Jean<script>alert(1)</script></p></body></html>")


class LignesDuHtmlTests(SimpleTestCase):
    def test_la_mise_en_forme_en_ligne_est_gardee(self):
        lignes = lignes_du_html("<p>Un <b>gras</b> et <i>italique</i>,<br>puis <u>souligné</u></p>")
        self.assertEqual([ligne.texte for ligne in lignes], ["Un gras et italique,", "puis souligné"])
        self.assertEqual(lignes[0].html, "Un <strong>gras</strong> et <em>italique</em>,")
        self.assertEqual(lignes[1].html, "puis <u>souligné</u>")

    def test_une_mise_en_forme_a_cheval_sur_deux_lignes_est_fermee_puis_rouverte(self):
        lignes = lignes_du_html("<p><b>un<br>deux</b> trois</p>")
        self.assertEqual([ligne.html for ligne in lignes], ["<strong>un</strong>", "<strong>deux</strong> trois"])

    def test_un_lien_sans_cible_sure_ne_garde_que_son_texte(self):
        lignes = lignes_du_html('<p><a href="javascript:x()">clic</a> <a href="https://a.be?x=1&amp;y=2">ok</a></p>')
        self.assertEqual(lignes[0].html, 'clic <a href="https://a.be?x=1&amp;y=2">ok</a>')

    def test_le_texte_est_echappe(self):
        lignes = lignes_du_html("<p>X &lt;x@y.be&gt; &amp; 1 &lt; 2</p>")
        self.assertEqual(lignes[0].texte, "X <x@y.be> & 1 < 2")
        self.assertEqual(lignes[0].html, "X &lt;x@y.be&gt; &amp; 1 &lt; 2")

    def test_une_citation_a_sa_profondeur(self):
        lignes = lignes_du_html("<div>Merci.</div><div>Le 1 sept. 2026 à 10:00, X a écrit :"
                                "<blockquote><div>Cité</div><blockquote>Plus ancien</blockquote></blockquote></div>")
        self.assertEqual([(ligne.texte, ligne.profondeur) for ligne in lignes if not ligne.vide], [
            ("Merci.", 0), ("Le 1 sept. 2026 à 10:00, X a écrit :", 0), ("Cité", 1), ("Plus ancien", 2)])

    def test_les_lignes_vides_sont_resserrees(self):
        lignes = lignes_du_html("<p></p><p>a</p><p></p><div><br></div><p></p><p>b</p><p></p>")
        self.assertEqual([ligne.texte for ligne in lignes], ["a", "", "b"])

    def test_un_pre_garde_ses_lignes(self):
        self.assertEqual([ligne.texte for ligne in lignes_du_html("<pre>a\nb\n\nc</pre>")], ["a", "b", "", "c"])

    def test_une_liste_a_des_puces(self):
        self.assertEqual([ligne.texte for ligne in lignes_du_html("<ul><li>un</li><li>deux</li></ul>")],
                         ["• un", "• deux"])


class RenduTests(SimpleTestCase):
    def test_les_paragraphes_et_les_citations(self):
        lignes = [Ligne("a", "a"), Ligne("b", "b"), Ligne("", ""), Ligne("c", "c", 1), Ligne("d", "d", 2)]
        self.assertEqual(en_html(lignes),
                         "<p>a<br>b</p><blockquote><p>c</p><blockquote><p>d</p></blockquote></blockquote>")

    def test_le_texte_brut_cite_devient_une_citation(self):
        self.assertEqual(en_html(lignes_du_texte("Oui.\n\n> Non ?\n>> Peut-être")),
                         "<p>Oui.</p><blockquote><p>Non ?</p><blockquote><p>Peut-être</p></blockquote></blockquote>")


class SansCitationsTests(SimpleTestCase):
    A_ECRIT = "Le 1 sept. 2026 à 10:00, X <x@y.be> a écrit :"

    def textes(self, lignes):
        return [ligne.texte for ligne in lignes]

    def test_une_reponse_au_dessus_de_la_citation_est_coupee(self):
        lignes = lignes_du_texte("Je refuse.\n\n%s\n> Acceptez-vous ?\n> X" % self.A_ECRIT)
        self.assertEqual(self.textes(sans_citations(lignes)), ["Je refuse."])

    def test_des_reponses_glissees_dans_la_citation_gardent_tout(self):
        """Répondre point par point : couper perdrait les réponses."""
        texte = "Voir ci-dessous.\n\n%s\n> Acceptez-vous le devis ?\nNon.\n> Et le délai ?\nOui." % self.A_ECRIT
        lignes = lignes_du_texte(texte)
        self.assertEqual(sans_citations(lignes), lignes)

    def test_une_reponse_sous_la_citation_garde_tout(self):
        lignes = lignes_du_texte("Bonjour,\n\n%s\n> Acceptez-vous ?\n\nNon, je refuse." % self.A_ECRIT)
        self.assertEqual(sans_citations(lignes), lignes)

    def test_en_html_aussi(self):
        lignes = lignes_du_html("<div>Voir ci-dessous.</div><div>%s<blockquote>Acceptez-vous ?</blockquote>"
                                "<div><b>Non.</b></div><blockquote>Et le délai ?</blockquote></div>"
                                % self.A_ECRIT.replace("<", "&lt;").replace(">", "&gt;"))
        self.assertEqual(sans_citations(lignes), lignes)
        coupees = lignes_du_html("<div>Je refuse.</div><div>%s<blockquote>Acceptez-vous ?</blockquote></div>"
                                 % self.A_ECRIT.replace("<", "&lt;").replace(">", "&gt;"))
        self.assertEqual(self.textes(sans_citations(coupees)), ["Je refuse."])

    def test_une_citation_outlook_sans_marque_est_coupee(self):
        lignes = lignes_du_texte("Je refuse.\n\n________________________________\nDe : X <x@y.be>\n"
                                 "Envoyé : mardi 1 septembre 2026 10:00\nÀ : Moi\nObjet : Devis\n\nAcceptez-vous ?")
        self.assertEqual(self.textes(sans_citations(lignes)), ["Je refuse."])


class DescriptionTests(SimpleTestCase):
    def test_un_courriel_direct_vient_de_son_html(self):
        a = analyser(courriel("Devis", TEXTE, html=HTML).as_bytes())
        description = description_de(a)
        self.assertIn("<strong>refusé</strong>", description)
        self.assertIn('<a href="https://exemple.be/devis" rel="noopener noreferrer nofollow">le devis</a>',
                      description)
        for absent in ("script", "alert", "onclick", "style", "color"):
            self.assertNotIn(absent, description)

    def test_sans_html_le_texte_comme_avant(self):
        a = analyser(courriel("Devis", "Bonjour,\n\nNon.").as_bytes())
        self.assertEqual(description_de(a), "<p>Bonjour,</p><p>Non.</p>")

    def test_un_html_qui_ne_ressemble_pas_au_texte_n_est_pas_pris(self):
        """Une partie HTML qui dit autre chose que le texte : on ne sait pas laquelle
        est la bonne, la description reste celle du texte."""
        a = analyser(courriel("Devis", TEXTE, html="<p>Newsletter : <b>promotions</b> du mois</p>").as_bytes())
        description = description_de(a)
        self.assertIn("Le devis est refusé.", description)
        self.assertNotIn("promotions", description)

    def test_un_transfert_dans_le_texte_ne_garde_que_le_courriel_d_origine(self):
        entete = ("De : ACME Bat <contact@acme-bat.example>\nEnvoyé : mardi 25 août 2026 14:31\n"
                  "À : Jean <jdupont@exemple.be>\nObjet : Devis")
        texte = "Pour le dossier.\n\n________________________________\n%s\n\n%s" % (entete, TEXTE)
        html = ("<p>Pour le <i>dossier</i>.</p><hr><p><b>De :</b> ACME Bat &lt;contact@acme-bat.example&gt;<br>"
                "<b>Envoyé :</b> mardi 25 août 2026 14:31<br><b>À :</b> Jean &lt;jdupont@exemple.be&gt;<br>"
                "<b>Objet :</b> Devis</p><div>&nbsp;</div>" + HTML)  # comme Outlook : une ligne vide
        couches = analyser_en_couches(courriel("TR: Devis", texte, html=html, a="avocate@x.be").as_bytes(),
                                      ignorer=[ALIAS])
        origine, transfert = couches
        description = description_de(origine)
        self.assertTrue(description.startswith("<p>Bonjour,</p><p>Le devis est <strong>refusé</strong>"))
        self.assertNotIn("ACME Bat", description)
        self.assertNotIn("dossier", description)
        self.assertEqual(description_de(transfert), "<p>Pour le <em>dossier</em>.</p>")


class ImportTests(MediaMixin, TestCase):
    def test_l_import_ecrit_la_description_mise_en_forme(self):
        dossier = make_dossier(make_user("owner"))
        evenement = importer(dossier, analyser(courriel("Devis", TEXTE, html=HTML).as_bytes()))
        self.assertIn("<strong>refusé</strong>", evenement.description)


class CoucheRetrouveeTests(SimpleTestCase):
    def test_le_courriel_cite_seul_n_est_pas_pris_pour_la_reponse(self):
        """Le texte d'une réponse contient toute sa citation : le morceau du seul courriel
        cité lui ressemble presque autant. Il ne commence pas comme elle (ACMEBAT n° 12)."""
        from types import SimpleNamespace

        from events.mise_en_forme import lignes_de_couche
        cite = " ".join("mot%d" % k for k in range(300))
        entete = "De : X <x@y.be>\nEnvoyé : mardi 1 septembre 2026 10:00\nÀ : Moi\nObjet : Devis"
        # La réponse s'écrit autrement en texte brut et en HTML (signature en tableau,
        # liens en clair) : presque aucun de ses mots ne se retrouve.
        reponse_texte = "Voir ci-dessous. " + " ".join("brut%d" % k for k in range(40))
        reponse_html = "Voir ci-dessous. " + " ".join("html%d" % k for k in range(40))
        texte = "%s\n\n%s\n\n%s" % (reponse_texte, entete, cite)
        html = "<p>%s</p><p>%s</p><div>&nbsp;</div><p>%s</p>" % (
            reponse_html, entete.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"), cite)
        eml = courriel("RE: Devis", texte, html=html).as_bytes()
        lignes = lignes_de_couche(SimpleNamespace(eml=eml, texte=texte))
        # Le bon morceau, ou aucun (la description vient alors du texte) ; jamais le cité seul.
        self.assertTrue(lignes is None or lignes[0].texte.startswith("Voir ci-dessous."))
