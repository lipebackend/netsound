"""Servidor web local (somente 127.0.0.1): página com sintetizador Web Audio.

Rotas:
  /                 página (HTML/JS)
  /api/config       timbres, classes e constantes do mapeamento (JSON)
  /api/estado       último quadro + controles de som (JSON, para "polling")
  /api/eventos      fluxo Server-Sent Events (um quadro por intervalo)
  /api/saude        {"ok": true}
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .mapeamento import CONFIG, Mapeador

DIR_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class Difusor:
    """Guarda o último quadro e acorda os clientes SSE quando chega outro."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.ultimo = None

    def publicar(self, quadro):
        with self.cond:
            self.seq += 1
            self.ultimo = json.dumps(quadro, ensure_ascii=False, separators=(",", ":"))
            self.cond.notify_all()

    def esperar(self, seq, timeout):
        with self.cond:
            if self.seq == seq:
                self.cond.wait(timeout)
            return self.seq, self.ultimo


def laco_amostragem(fonte_quadros, difusor, intervalo, parar):
    """fonte_quadros: objeto com .amostrar() (real) ou .proximo() (simulado)."""
    mapeador = Mapeador()
    amostrar = getattr(fonte_quadros, "amostrar", None) or fonte_quadros.proximo
    if hasattr(fonte_quadros, "amostrar"):
        fonte_quadros.amostrar()  # linha de base dos contadores
    proximo = time.monotonic() + intervalo
    while not parar.is_set():
        espera = proximo - time.monotonic()
        if espera > 0 and parar.wait(espera):
            break
        proximo += intervalo
        try:
            q = amostrar()
        except Exception as e:  # nunca derruba o servidor por falha de leitura
            q = {"erro": str(e)}
        if "erro" not in q:
            q["som"] = mapeador.processar(q)
        difusor.publicar(q)


def criar_servidor(porta, difusor, intervalo, demo):
    estaticos = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "application/javascript; charset=utf-8"),
        "/gravador.js": ("gravador.js", "application/javascript; charset=utf-8"),
        "/estilo.css": ("estilo.css", "text/css; charset=utf-8"),
    }
    conteudo = {}
    for rota, (arq, tipo) in estaticos.items():
        with open(os.path.join(DIR_WEB, arq), "rb") as f:
            conteudo[rota] = (f.read(), tipo)
    csp = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
           "img-src 'self' data:; media-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    config = dict(CONFIG)
    config["intervalo"] = intervalo
    config["demo"] = demo
    config_bytes = json.dumps(config, ensure_ascii=False).encode("utf-8")
    hosts_ok = {"127.0.0.1:%d" % porta, "localhost:%d" % porta, "[::1]:%d" % porta}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "netsound"

        def log_message(self, fmt, *args):
            pass

        def _enviar(self, codigo, tipo, corpo, extra=None):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            # Proteção contra DNS rebinding: só aceita Host local.
            if self.headers.get("Host", "") not in hosts_ok:
                return self._enviar(403, "text/plain; charset=utf-8", "Host não permitido.".encode())
            caminho = self.path.split("?", 1)[0]
            if caminho in conteudo:
                corpo, tipo = conteudo[caminho]
                return self._enviar(200, tipo, corpo, {"Content-Security-Policy": csp})
            if caminho == "/api/config":
                return self._enviar(200, "application/json; charset=utf-8", config_bytes)
            if caminho == "/favicon.ico":
                return self._enviar(204, "image/x-icon", b"")
            if caminho == "/api/saude":
                return self._enviar(200, "application/json", b'{"ok":true}')
            if caminho == "/api/estado":
                _, ult = difusor.esperar(-1, 0)
                corpo = (ult or '{"erro":"sem dados ainda"}').encode("utf-8")
                return self._enviar(200, "application/json; charset=utf-8", corpo)
            if caminho == "/api/eventos":
                return self._sse()
            return self._enviar(404, "text/plain; charset=utf-8", "Não encontrado.".encode())

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            seq = 0
            try:
                self.wfile.write(b": conectado\n\n")
                self.wfile.flush()
                while True:
                    novo, dados = difusor.esperar(seq, 10)
                    if novo == seq or dados is None:
                        self.wfile.write(b": ping\n\n")
                    else:
                        seq = novo
                        self.wfile.write(b"data: " + dados.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    class Servidor(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return Servidor(("127.0.0.1", porta), Handler)
