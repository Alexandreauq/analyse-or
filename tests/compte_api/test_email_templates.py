# Tests du gabarit HTML partagé des e-mails à bouton d'action.
from compte_api.email_templates import gabarit_bouton


def test_gabarit_bouton_contient_le_titre_le_texte_et_le_lien():
    html = gabarit_bouton("Confirme ton adresse", "Un dernier clic.", "Confirmer", "https://site.test/?verify=abc")
    assert "Confirme ton adresse" in html
    assert "Un dernier clic." in html
    assert "Confirmer" in html
    # bouton (href) + lien de secours (href + texte affiché)
    assert html.count("https://site.test/?verify=abc") == 3


def test_gabarit_bouton_echappe_le_html_dans_le_texte():
    html = gabarit_bouton("<b>Titre</b>", "Et toi & moi", "OK", "https://site.test/")
    assert "<b>Titre</b>" not in html
    assert "&lt;b&gt;Titre&lt;/b&gt;" in html
    assert "Et toi &amp; moi" in html


def test_gabarit_bouton_echappe_les_guillemets_dans_l_url():
    html = gabarit_bouton("Titre", "Intro", "OK", 'https://site.test/?x="injected"')
    assert 'href="https://site.test/?x="injected""' not in html
    assert "&quot;" in html


def test_gabarit_bouton_sans_expiration_ni_mention_naffiche_rien_a_la_place():
    html = gabarit_bouton("Titre", "Intro", "OK", "https://site.test/")
    assert "expire" not in html.lower()


def test_gabarit_bouton_avec_expiration_et_mention():
    html = gabarit_bouton("Titre", "Intro", "OK", "https://site.test/",
                          expiration="Ce lien expire dans 24 heures.",
                          mention_bas="Ignore ce message si besoin.")
    assert "Ce lien expire dans 24 heures." in html
    assert "Ignore ce message si besoin." in html
