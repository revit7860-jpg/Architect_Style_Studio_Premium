"""Local Gemini proxy and static server. Python 3.10+, no third-party dependencies.

Hardening compared with V2:
- serves only index.html (no other file of the folder is reachable);
- rejects requests whose Host or Origin is not the local server (DNS rebinding / cross-site POST);
- requires a custom header on POST, which a cross-site page cannot send without a CORS preflight;
- Gemini model name configurable per request (validated) or with GEMINI_MODEL;
- opens the browser only after the server socket is listening.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json, os, re, sys, threading, webbrowser, urllib.request, urllib.error, base64
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
KEY = os.environ.get('GEMINI_API_KEY', '')
HOST = '127.0.0.1'
PORT = int(os.environ.get('APP_PORT', '8765'))
ALLOWED_HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
ALLOWED_ORIGINS = {f'http://{h}' for h in ALLOWED_HOSTS}
MODEL_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
INSTRUCTION = '''Analyse ce plan architectural 2D. Retourne UNIQUEMENT un objet JSON valide avec les champs suivants, en français : summary (chaîne), rooms (liste de chaînes indiquant chaque espace observé et sa position relative), openings (chaîne), structure (chaîne), wet_areas (chaîne), circulation (chaîne), dimensions (chaîne), furniture (liste de chaînes : chaque meuble ou équipement réellement dessiné avec sa position, sans en ajouter ; ne liste ni plantes, ni tapis, ni objets de décoration), bedrooms (entier : nombre de chambres, 0 si studio ou illisible), uncertainties (liste de chaînes). Dans chaque chaîne et chaque élément de liste, préfixe par « [supposé] » toute information qui n'est pas directement lisible sur le plan (meuble douteux, fonction déduite, élément partiellement caché). Décris seulement ce qui est visible avec une distinction nette entre observation et hypothèse. Ne devine aucune cote illisible, aucune fonction non étiquetée ni réseau non visible. Les points d'évacuation marqués doivent être identifiés s'ils sont visibles. Ne prétends pas certifier la conformité du plan.'''


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(BASE), **k)

    # ---- common headers -------------------------------------------------
    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')  # no stale page: Ctrl+F5 no longer needed
        super().end_headers()

    # ---- checks ---------------------------------------------------------
    def host_ok(self):
        return self.headers.get('Host', '') in ALLOWED_HOSTS

    def forbid(self, message):
        return self.respond(403, {'error': message})

    # ---- GET / HEAD : index.html only -----------------------------------
    def _serve_index(self, head=False):
        if not self.host_ok():
            return self.forbid('Hôte non autorisé.')
        path = self.path.split('?', 1)[0].split('#', 1)[0]
        if path not in ('/', '/index.html'):
            return self.send_error(404)
        self.path = '/index.html'
        if head:
            return SimpleHTTPRequestHandler.do_HEAD(self)
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_GET(self):
        self._serve_index()

    def do_HEAD(self):
        self._serve_index(head=True)

    # ---- POST /api/analyze ----------------------------------------------
    def do_POST(self):
        if self.path != '/api/analyze':
            return self.send_error(404)
        if not self.host_ok():
            return self.forbid('Hôte non autorisé.')
        origin = self.headers.get('Origin')
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return self.forbid('Origine non autorisée.')
        if self.headers.get('X-Requested-With') != 'studio':
            return self.forbid('En-tête de requête manquant.')
        if not KEY:
            return self.respond(503, {'error': 'GEMINI_API_KEY non configurée sur le serveur local.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.respond(400, {'error': 'Content-Length invalide.'})
        if length < 1 or length > 16_000_000:
            return self.respond(413, {'error': 'Fichier trop volumineux (maximum environ 10 Mo).'})
        try:
            data = json.loads(self.rfile.read(length))
            mime = data.get('mime', '')
            payload = data.get('data', '')
            model = data.get('model') or DEFAULT_MODEL
            if not MODEL_RE.match(model):
                raise ValueError('Nom de modèle invalide.')
            if mime not in ('image/png', 'image/jpeg', 'image/webp', 'application/pdf'):
                raise ValueError('Format non pris en charge pour analyse automatique.')
            binary = base64.b64decode(payload, validate=True)
            if len(binary) > 10_000_000:
                raise ValueError('Fichier trop volumineux (10 Mo max).')
            request_body = {
                'contents': [{'role': 'user', 'parts': [{'text': INSTRUCTION}, {'inline_data': {'mime_type': mime, 'data': payload}}]}],
                'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.1},
            }
            url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
            req = urllib.request.Request(
                url, data=json.dumps(request_body).encode(),
                headers={'Content-Type': 'application/json', 'x-goog-api-key': KEY}, method='POST')
            with urllib.request.urlopen(req, timeout=90) as response:
                result = json.load(response)
            raw = ''.join(p.get('text', '') for c in result.get('candidates', []) for p in c.get('content', {}).get('parts', []))
            parsed = json.loads(raw)
            return self.respond(200, {'analysis': parsed, 'model': model})
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode('utf-8', 'replace')
            return self.respond(502, {'error': f'Erreur Gemini HTTP {exc.code}: {detail}'})
        except Exception as exc:
            return self.respond(400, {'error': str(exc)})

    def respond(self, status, content):
        body = json.dumps(content, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    url = f'http://{HOST}:{PORT}'
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f'Impossible de démarrer sur le port {PORT} ({exc}). Fermez l’ancienne instance (Ctrl+C) puis relancez.')
        sys.exit(1)
    print(f'Architect Style Studio V2.3 : {url} | Mode analyse IA : {"actif" if KEY else "inactif"} | Modèle par défaut : {DEFAULT_MODEL}')
    if os.environ.get('NO_BROWSER') != '1':
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()  # socket already listening: no race
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nArrêt du serveur.')
