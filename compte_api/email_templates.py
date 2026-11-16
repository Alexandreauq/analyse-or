# compte_api/email_templates.py
# Gabarit HTML partagé pour les e-mails à bouton d'action (confirmation,
# réinitialisation...). Styles entièrement en ligne et mise en page par
# tableaux : les clients mail (Outlook en tête) ignorent <style> et le
# CSS moderne, donc rien ne doit dépendre d'eux pour rester lisible.
# Polices de secours seulement (Georgia/Arial) : une police web externe
# n'est pas chargée de façon fiable dans un e-mail.
import html as _html

# Identité visuelle sombre du site (docs/index.html --bg-0/--gold/Newsreader).
_BG = "#0a0e1a"
_CARD_BG = "#0f1524"
_CARD_BORDER = "#1e2740"
_TEXT = "#e8ecf5"
_MUTED = "#9aa6c4"
_FAINT = "#5f688a"
_GOLD = "#b99a68"
_GOLD_INK = "#16130c"
_LINK = "#8c99c2"

_SERIF = "Georgia, 'Times New Roman', serif"
_SANS = "Arial, Helvetica, sans-serif"


def gabarit_bouton(titre: str, intro: str, bouton_texte: str, url: str,
                   expiration: str | None = None, mention_bas: str | None = None) -> str:
    """HTML d'un e-mail à bouton d'action unique (fond sombre, accent or).
    `titre`/`intro`/`bouton_texte`/`mention_bas` sont échappés (texte brut
    attendu) ; `url` est insérée telle quelle dans deux attributs href."""
    titre_e = _html.escape(titre)
    intro_e = _html.escape(intro)
    bouton_e = _html.escape(bouton_texte)
    url_e = _html.escape(url, quote=True)
    expiration_html = (
        f'<tr><td align="center" style="padding-top:18px;font-family:{_SANS};'
        f'font-size:12px;color:{_FAINT};">{_html.escape(expiration)}</td></tr>'
    ) if expiration else ""
    mention_html = (
        f'<tr><td align="center" style="padding-top:34px;border-top:1px solid {_CARD_BORDER};'
        f'margin-top:34px;font-family:{_SANS};font-size:11.5px;color:#454e6e;">'
        f'<div style="padding-top:20px;">{_html.escape(mention_bas)}</div></td></tr>'
    ) if mention_bas else ""
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_BG};">
<tr><td align="center" style="padding:44px 16px;">
<table role="presentation" width="420" cellpadding="0" cellspacing="0" style="max-width:420px;width:100%;background-color:{_CARD_BG};border:1px solid {_CARD_BORDER};border-radius:16px;">
<tr><td align="center" style="padding:40px 32px 0;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="width:40px;height:40px;border-radius:50%;border:2px solid {_GOLD};font-size:0;line-height:40px;">&nbsp;</td></tr></table>
</td></tr>
<tr><td align="center" style="padding:18px 32px 0;font-family:{_SERIF};font-size:20px;color:{_TEXT};">Nomi</td></tr>
<tr><td align="center" style="padding:28px 32px 0;font-family:{_SERIF};font-weight:normal;font-size:24px;color:{_TEXT};">{titre_e}</td></tr>
<tr><td align="center" style="padding:14px 32px 0;font-family:{_SANS};font-size:14px;line-height:1.65;color:{_MUTED};">{intro_e}</td></tr>
<tr><td align="center" style="padding:30px 32px 0;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td style="border-radius:999px;background-color:{_GOLD};">
<a href="{url_e}" style="display:inline-block;padding:13px 34px;font-family:{_SANS};font-weight:bold;font-size:14.5px;color:{_GOLD_INK};text-decoration:none;">{bouton_e}</a>
</td></tr></table>
</td></tr>
{expiration_html}
<tr><td align="center" style="padding:26px 32px 0;font-family:{_SANS};font-size:12px;line-height:1.6;color:{_FAINT};word-break:break-all;">
Le bouton ne marche pas ? Colle ce lien dans ton navigateur :<br>
<a href="{url_e}" style="color:{_LINK};">{url_e}</a>
</td></tr>
{mention_html}
<tr><td style="padding-bottom:32px;"></td></tr>
</table>
</td></tr>
</table>
"""
