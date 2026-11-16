# scripts/dev_compte.py
# Serveur de développement : sert docs/ sur http://127.0.0.1:8000 et relaie
# /api/compte/* vers le service compte local (127.0.0.1:8447) en ajoutant
# l'en-tête secret, comme le fera le rewrite Vercel en production.
#
#   .venv-compte/Scripts/uvicorn compte_api.app:app --port 8447   (terminal 1)
#   python scripts/dev_compte.py                                   (terminal 2)
#
# Bibliothèque standard uniquement. Réservé au poste de développement.
import http.client
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1] / "docs"
PREFIXE = "/api/compte"
SERVICE_HOTE, SERVICE_PORT = "127.0.0.1", int(os.environ.get("COMPTE_PORT", "8447"))
SECRET = os.environ.get("COMPTE_PROXY_SECRET", "dev-secret")
EN_TETES_TRANSMIS = ("content-type", "cookie", "x-requested-with", "accept")
EN_TETES_RETOUR_IGNORES = {"transfer-encoding", "connection", "content-length", "server", "date"}


class Gestionnaire(SimpleHTTPRequestHandler):
    def _relayer(self):
        if not self.path.startswith(PREFIXE + "/"):
            return False
        cible = self.path[len(PREFIXE):]
        longueur = int(self.headers.get("content-length") or 0)
        corps = self.rfile.read(longueur) if longueur else None
        en_tetes = {k: v for k, v in self.headers.items() if k.lower() in EN_TETES_TRANSMIS}
        en_tetes["X-Compte-Proxy"] = SECRET
        en_tetes["X-Forwarded-For"] = self.client_address[0]
        connexion = http.client.HTTPConnection(SERVICE_HOTE, SERVICE_PORT, timeout=120)
        try:
            connexion.request(self.command, cible, body=corps, headers=en_tetes)
            reponse = connexion.getresponse()
        except OSError:
            self.send_error(502, "Service compte injoignable (uvicorn lancé sur le port 8447 ?)")
            return True
        self.send_response(reponse.status)
        for cle, valeur in reponse.getheaders():
            if cle.lower() not in EN_TETES_RETOUR_IGNORES:
                self.send_header(cle, valeur)
        self.send_header("Connection", "close")
        self.end_headers()
        # Lecture par morceaux : les réponses en flux (SSE de l'assistant IA)
        # arrivent au navigateur au fil de l'eau.
        while True:
            morceau = reponse.read1(8192) if hasattr(reponse, "read1") else reponse.read(8192)
            if not morceau:
                break
            self.wfile.write(morceau)
            self.wfile.flush()
        connexion.close()
        return True

    def do_GET(self):
        if not self._relayer():
            super().do_GET()

    def do_POST(self):
        if not self._relayer():
            self.send_error(405)

    do_PUT = do_PATCH = do_DELETE = do_POST

    def end_headers(self):
        # Pas de cache en développement : chaque rechargement prend la dernière version.
        if not self.path.startswith(PREFIXE):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    serveur = ThreadingHTTPServer(("127.0.0.1", port), partial(Gestionnaire, directory=str(RACINE)))
    print(f"Site : http://127.0.0.1:{port}  (relais {PREFIXE} -> {SERVICE_HOTE}:{SERVICE_PORT})")
    serveur.serve_forever()


if __name__ == "__main__":
    main()
