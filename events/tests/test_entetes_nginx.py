"""Un en-tête de sécurité n'a qu'une source par réponse : Django ou nginx, jamais les deux.

Constaté en production le 2026-10-11 : HSTS, `X-Content-Type-Options` et
`Referrer-Policy` partaient deux fois sur chaque page. Pour HSTS, deux durées
différentes. Pour `Referrer-Policy`, pire : le navigateur retient la dernière
valeur, celle de nginx (`same-origin`), et défaisait le `no-referrer` que
`share_views` pose sur les pages d'un lien de partage.

La règle : ce que Django sert, Django l'en-tête (il connaît la page) ; nginx ne
pose ces en-têtes que sur ce qu'il sert seul, les fichiers statiques et les
pièces (X-Accel-Redirect).
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

CONF = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "chronos.foxugly.com.conf"
POSES_PAR_DJANGO = ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy")


def blocs(texte):
    """{en-tête du bloc: [directives propres à ce bloc]} pour chaque `server` et `location`."""
    resultat, pile, serveurs = {}, [], 0
    for ligne in texte.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if ligne.endswith("{"):
            entete = ligne[:-1].strip()
            if entete == "server":
                serveurs += 1
                entete = f"server {serveurs}"
            pile.append(entete)
            resultat.setdefault(" > ".join(pile), [])
        elif ligne == "}":
            pile.pop()
        elif ligne and pile:
            resultat[" > ".join(pile)].append(ligne)
    return resultat


def entetes(directives):
    return {m.group(1) for d in directives if (m := re.match(r"add_header\s+(\S+)", d))}


class EntetesNginxTests(SimpleTestCase):
    def setUp(self):
        self.blocs = blocs(CONF.read_text(encoding="utf-8"))
        self.https = next(c for c, d in self.blocs.items() if c.startswith("server ") and "listen 443 ssl http2;" in d)

    def bloc(self, location):
        return self.blocs[f"{self.https} > {location}"]

    def test_le_niveau_server_ne_double_pas_django(self):
        """`location /` et `/health/` n'ont pas d'add_header : ils héritent du server."""
        self.assertFalse(entetes(self.blocs[self.https]) & set(POSES_PAR_DJANGO))

    def test_les_locations_proxifiees_n_en_posent_pas(self):
        for location in ("location /", "location = /health/"):
            with self.subTest(location=location):
                self.assertFalse(entetes(self.bloc(location)) & set(POSES_PAR_DJANGO))

    def test_les_statiques_et_les_pieces_ont_les_leurs(self):
        for location in ("location ^~ /static/", "location ^~ /_pieces_internes/"):
            with self.subTest(location=location):
                self.assertTrue(
                    {"Strict-Transport-Security", "X-Content-Type-Options"} <= entetes(self.bloc(location))
                )

    def test_une_seule_duree_hsts(self):
        """Django et nginx annoncent la même durée : celle que les navigateurs ont déjà retenue."""
        texte = CONF.read_text(encoding="utf-8")
        durees = set(re.findall(r'Strict-Transport-Security "max-age=(\d+)', texte))
        reglages = (Path(__file__).resolve().parents[2] / "config" / "settings.py").read_text(encoding="utf-8")
        defaut = re.search(r'"SECURE_HSTS_SECONDS", "(\d+)"', reglages).group(1)
        self.assertEqual(durees, {defaut})
