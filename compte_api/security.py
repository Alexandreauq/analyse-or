# compte_api/security.py
# Mots de passe (Argon2id) et jetons aléatoires (sessions, liens d'e-mail).
# Un jeton n'est jamais stocké en clair : seul son SHA-256 va en base.
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(mot_de_passe: str) -> str:
    return _hasher.hash(mot_de_passe)


def verify_password(hache: str | None, mot_de_passe: str) -> bool:
    if not hache:
        return False
    try:
        return _hasher.verify(hache, mot_de_passe)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(hache: str) -> bool:
    return _hasher.check_needs_rehash(hache)


def hacher_jeton(clair: str) -> str:
    return hashlib.sha256(clair.encode("utf-8")).hexdigest()


def nouveau_jeton() -> tuple[str, str]:
    """(jeton clair à donner au navigateur ou à mettre dans un lien, son hachage à stocker)."""
    clair = secrets.token_urlsafe(32)
    return clair, hacher_jeton(clair)


def egal_temps_constant(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
