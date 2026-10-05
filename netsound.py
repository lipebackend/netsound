#!/usr/bin/env python3
"""netsound — Sonificação da rede.

Monitora a rede local de forma PASSIVA (somente leitura de /proc e `ss`/`ip`)
e transforma a atividade em música: ao vivo no navegador ou em arquivo .wav.

Uso:
  python3 netsound.py servir            # página ao vivo em http://127.0.0.1:8765
  python3 netsound.py gravar --segundos 60 --saida rede.wav
  python3 netsound.py demo --saida rede-demo.wav
  python3 netsound.py monitorar         # só mostra os números (sem som)
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nucleo import __version__  # noqa: E402
from nucleo.coletor import Amostrador  # noqa: E402
from nucleo.mapeamento import Mapeador  # noqa: E402
from nucleo.simulador import Simulador  # noqa: E402

TRADUCOES = {
    "usage: ": "uso: ",
    "options": "opções",
    "positional arguments": "argumentos posicionais",
    "show this help message and exit": "mostra esta ajuda e sai",
    "the following arguments are required: %s": "os seguintes argumentos são obrigatórios: %s",
    "invalid choice: %(value)r (choose from %(choices)s)": "opção inválida: %(value)r (escolha entre %(choices)s)",
    "invalid %(type)s value: %(value)r": "valor inválido para %(type)s: %(value)r",
    "unrecognized arguments: %s": "argumentos não reconhecidos: %s",
    "expected one argument": "esperava um argumento",
    "%(prog)s: error: %(message)s\n": "%(prog)s: erro: %(message)s\n",
}
try:  # traduz as mensagens padrão do argparse
    import argparse as _ap
    _orig = _ap._
    _ap._ = lambda s: TRADUCOES.get(s, _orig(s))
except Exception:
    pass


def fmt_taxa(bps):
    for un in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024 or un == "GB/s":
            return "%.1f %s" % (bps, un)
        bps /= 1024.0


def opcoes_coleta(p):
    p.add_argument("--intervalo", type=float, default=0.5, help="segundos entre amostras (padrão: 0,5)")
    p.add_argument("--fonte", choices=["auto", "ss", "proc"], default="auto",
                   help="origem das conexões: ss (se existir) ou /proc (padrão: auto)")
    p.add_argument("--interface", action="append", default=None,
                   help="usa só esta interface (pode repetir); padrão: todas, menos lo/docker/veth")
    p.add_argument("--incluir-loopback", action="store_true", help="inclui conexões locais (127.0.0.1/::1)")


def criar_amostrador(a):
    return Amostrador(fonte=a.fonte, ifaces=a.interface, incluir_loopback=a.incluir_loopback)


# ------------------------------------------------------------------ comandos
def cmd_servir(a):
    from nucleo.servidor import Difusor, criar_servidor, laco_amostragem
    fonte = Simulador(intervalo=a.intervalo) if a.demo else criar_amostrador(a)
    difusor = Difusor()
    parar = threading.Event()
    try:
        srv = criar_servidor(a.porta, difusor, a.intervalo, a.demo)
    except OSError as e:
        print("Erro: não foi possível usar a porta %d (%s). Tente --porta OUTRA." % (a.porta, e), file=sys.stderr)
        return 1
    th = threading.Thread(target=laco_amostragem, args=(fonte, difusor, a.intervalo, parar), daemon=True)
    th.start()
    url = "http://127.0.0.1:%d/" % a.porta
    print("netsound %s — %s" % (__version__, "MODO DEMO (tráfego simulado)" if a.demo else "monitorando a rede (somente leitura)"))
    print("Abra no navegador: %s" % url)
    print("Escutando apenas em 127.0.0.1. Pressione Ctrl+C para encerrar.")
    if a.abrir:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nEncerrando…")
    finally:
        parar.set()
        srv.server_close()
    return 0


def _renderizar_e_salvar(quadros, a, demo):
    from nucleo.sintese import Renderizador, escrever_wav
    mapeador = Mapeador()
    sons = [mapeador.processar(q) for q in quadros]
    dts = [q["dt"] for q in quadros]
    print("Sintetizando áudio (%d quadros, %.0f Hz)… pode levar alguns segundos." % (len(quadros), a.taxa))
    t0 = time.time()
    r = Renderizador(taxa=a.taxa, reverb=not a.sem_reverb, eco=not a.sem_reverb, cauda=0.0 if a.sem_cauda else 2.5)
    inter = r.renderizar(sons, dts)
    escrever_wav(a.saida, inter, a.taxa)
    dur = len(inter) / 2 / a.taxa
    print("Pronto: %s (%.1f s, %d Hz, estéreo 16 bits, %.1f KB) em %.1f s de processamento."
          % (a.saida, dur, a.taxa, os.path.getsize(a.saida) / 1024.0, time.time() - t0))
    ev = sum(len(s["eventos"]) for s in sons)
    print("Eventos sonoros gerados: %d" % ev)
    return 0


def cmd_gravar(a):
    if a.segundos <= 0:
        print("Erro: --segundos deve ser positivo.", file=sys.stderr)
        return 1
    if a.demo:
        sim = Simulador(intervalo=a.intervalo)
        n = int(round(a.segundos / a.intervalo))
        quadros = [sim.proximo() for _ in range(n)]
        return _renderizar_e_salvar(quadros, a, True)
    am = criar_amostrador(a)
    print("Amostrando a rede por %.0f s (somente leitura, nada é capturado)…" % a.segundos)
    am.amostrar()  # linha de base
    quadros = []
    n = int(round(a.segundos / a.intervalo))
    prox = time.monotonic()
    try:
        for i in range(n):
            prox += a.intervalo
            time.sleep(max(0.0, prox - time.monotonic()))
            q = am.amostrar()
            quadros.append(q)
            if i % max(1, int(2 / a.intervalo)) == 0:
                print("  t=%5.1fs  ↓ %-11s ↑ %-11s conexões=%d novas=%d vizinhos=%d" % (
                    q["t"], fmt_taxa(q["entrada_bps"]), fmt_taxa(q["saida_bps"]),
                    q["conexoes"]["total"], len(q["novas"]), len(q["vizinhos"])))
    except KeyboardInterrupt:
        print("\nInterrompido: usando o que foi coletado até agora.")
    if not quadros:
        print("Nada coletado.", file=sys.stderr)
        return 1
    return _renderizar_e_salvar(quadros, a, False)


def cmd_demo(a):
    a.demo = True
    return cmd_gravar(a)


def cmd_monitorar(a):
    am = criar_amostrador(a)
    am.amostrar()
    fim = time.monotonic() + a.segundos
    try:
        while time.monotonic() < fim:
            time.sleep(a.intervalo)
            q = am.amostrar()
            c = q["conexoes"]
            print("t=%6.1fs ↓ %-11s ↑ %-11s | conexões %3d (https %d, http %d, dns %d, ssh %d, outros %d) | novas %d | vizinhos %d [%s]" % (
                q["t"], fmt_taxa(q["entrada_bps"]), fmt_taxa(q["saida_bps"]), c["total"],
                c["por_classe"]["https"], c["por_classe"]["http"], c["por_classe"]["dns"],
                c["por_classe"]["ssh"], c["por_classe"]["outros"], len(q["novas"]),
                len(q["vizinhos"]), q["fonte"]))
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="netsound.py", description="netsound — Sonificação da rede (monitoramento passivo, somente leitura).",
        epilog="Exemplos: python3 netsound.py servir | python3 netsound.py gravar --segundos 60 --saida rede.wav | "
               "python3 netsound.py demo")
    p.add_argument("--versao", action="version", version="netsound " + __version__, help="mostra a versão e sai")
    sub = p.add_subparsers(dest="comando", metavar="comando")

    s = sub.add_parser("servir", help="serve a página ao vivo em 127.0.0.1")
    s.add_argument("--porta", type=int, default=8765, help="porta HTTP local (padrão: 8765)")
    s.add_argument("--demo", action="store_true", help="usa tráfego simulado em vez da rede real")
    s.add_argument("--abrir", action="store_true", help="abre o navegador automaticamente")
    opcoes_coleta(s)
    s.set_defaults(func=cmd_servir)

    g = sub.add_parser("gravar", help="amostra a rede por N segundos e gera um .wav")
    g.add_argument("--segundos", type=float, default=30.0, help="duração da amostragem (padrão: 30)")
    g.add_argument("--saida", default="rede.wav", help="arquivo .wav de saída (padrão: rede.wav)")
    g.add_argument("--taxa", type=int, default=22050, help="taxa de amostragem em Hz (padrão: 22050)")
    g.add_argument("--demo", action="store_true", help="usa tráfego simulado em vez da rede real")
    g.add_argument("--sem-reverb", action="store_true", help="desliga reverb e eco")
    g.add_argument("--sem-cauda", action="store_true", help="não acrescenta a cauda final de reverb")
    opcoes_coleta(g)
    g.set_defaults(func=cmd_gravar)

    d = sub.add_parser("demo", help="gera um .wav a partir de tráfego simulado (não usa a rede)")
    d.add_argument("--segundos", type=float, default=30.0, help="duração (padrão: 30)")
    d.add_argument("--saida", default="rede-demo.wav", help="arquivo .wav de saída (padrão: rede-demo.wav)")
    d.add_argument("--taxa", type=int, default=22050, help="taxa de amostragem em Hz (padrão: 22050)")
    d.add_argument("--intervalo", type=float, default=0.5, help="segundos por quadro (padrão: 0,5)")
    d.add_argument("--sem-reverb", action="store_true", help="desliga reverb e eco")
    d.add_argument("--sem-cauda", action="store_true", help="não acrescenta a cauda final de reverb")
    d.set_defaults(func=cmd_demo)

    m = sub.add_parser("monitorar", help="mostra no terminal o que está sendo medido (sem áudio)")
    m.add_argument("--segundos", type=float, default=10.0, help="duração (padrão: 10)")
    opcoes_coleta(m)
    m.set_defaults(func=cmd_monitorar)

    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return 0
    if getattr(a, "intervalo", 0.5) < 0.1:
        p.error("--intervalo deve ser pelo menos 0,1")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
