#!/usr/bin/env python3
"""Testes automáticos (somente stdlib).  Uso: python3 testes/testar.py"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from nucleo import coletor, mapeamento, simulador, sintese  # noqa: E402
from nucleo.servidor import Difusor, criar_servidor, laco_amostragem  # noqa: E402


class TestColetor(unittest.TestCase):
    def test_enderecos_hex(self):
        self.assertEqual(coletor._ip4("0100007F"), "127.0.0.1")
        self.assertEqual(coletor._ip4("0101A8C0"), "192.168.1.1")
        self.assertEqual(coletor._ip6("00000000000000000000000001000000"), "::1")
        self.assertEqual(coletor._ip6("0000000000000000FFFF00000101A8C0"), "192.168.1.1")

    def test_proc_net_dev_real(self):
        d = coletor.ler_interfaces()
        self.assertTrue(d)
        for v in d.values():
            self.assertEqual(len(v), 4)

    def test_proc_arquivo_sintetico(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tcp", delete=False) as f:
            f.write("  sl  local_address rem_address   st\n"
                    "   0: 0101A8C0:C350 5DB8D822:01BB 01 00000000:00000000 00:00000000 00000000\n"   # ESTAB https
                    "   1: 0101A8C0:C351 5DB8D822:0050 06 00000000:00000000 00:00000000 00000000\n"   # TIME_WAIT
                    "   2: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n")  # LISTEN
        r = coletor._ler_proc_arquivo(f.name, "tcp", False)
        os.unlink(f.name)
        self.assertEqual(r, [("tcp", "192.168.1.1", 0xC350, "34.216.184.93", 443)])

    def test_arp_sintetico(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("IP address       HW type     Flags       HW address            Mask     Device\n"
                    "192.168.0.1      0x1         0x2         aa:bb:cc:dd:ee:01     *        eth0\n"
                    "192.168.0.9      0x1         0x0         00:00:00:00:00:00     *        eth0\n")
        r = coletor.vizinhos_arp(f.name)
        os.unlink(f.name)
        self.assertEqual(list(r), ["aa:bb:cc:dd:ee:01"])

    def test_amostrador_real_e_fontes(self):
        for fonte in ("proc", "auto"):
            a = coletor.Amostrador(fonte=fonte)
            a.amostrar()
            time.sleep(0.3)
            q = a.amostrar()
            self.assertGreaterEqual(q["entrada_bps"], 0)
            self.assertEqual(set(q["conexoes"]["por_classe"]), set(mapeamento.ORDEM_CLASSES))

    def test_classificacao(self):
        c = mapeamento.classificar
        self.assertEqual(c(50000, 443), "https")
        self.assertEqual(c(50000, 80), "http")
        self.assertEqual(c(50000, 53), "dns")
        self.assertEqual(c(22, 50000), "ssh")
        self.assertEqual(c(50000, 6000), "outros")


class TestMapeamento(unittest.TestCase):
    def test_nivel_e_notas(self):
        self.assertEqual(mapeamento.nivel_taxa(10), 0)
        self.assertAlmostEqual(mapeamento.nivel_taxa(2e7), 1.0, places=2)
        self.assertAlmostEqual(mapeamento.nota_pent(0, 57), 220.0, places=3)
        self.assertEqual(mapeamento.nome_nota(440.0), "Lá4")

    def test_pico_gera_acorde_e_pans(self):
        sim = simulador.Simulador()
        m = mapeamento.Mapeador()
        picos = 0
        for _ in range(80):
            som = m.processar(sim.proximo())
            if som["info"]["pico"]:
                picos += 1
                self.assertTrue(any(e["timbre"] == "acorde" for e in som["eventos"]))
            v = {x["id"]: x for x in som["vozes"]}
            self.assertLess(v["entrada"]["pan"], 0)
            self.assertGreater(v["saida"]["pan"], 0)
        self.assertGreaterEqual(picos, 2)

    def test_pentatonica(self):
        permitidas = {(57 + s) % 12 for s in mapeamento.ESCALA}
        sim = simulador.Simulador()
        m = mapeamento.Mapeador()
        import math
        for _ in range(60):
            for v in m.processar(sim.proximo())["vozes"]:
                if v["id"] in ("entrada", "saida"):
                    midi = round(69 + 12 * math.log2(v["freq"] / 440))
                    self.assertIn(midi % 12, permitidas)


class TestSintese(unittest.TestCase):
    def test_render_curto(self):
        sim = simulador.Simulador()
        m = mapeamento.Mapeador()
        sons = [m.processar(sim.proximo()) for _ in range(12)]
        inter = sintese.Renderizador(taxa=11025, cauda=1.0).renderizar(sons, [0.5] * 12)
        self.assertEqual(len(inter) // 2, int(round((6 + 1.0) * 11025)))
        self.assertLess(max(abs(x) for x in inter), 32767)
        self.assertGreater(max(abs(x) for x in inter), 5000)


class TestServidor(unittest.TestCase):
    def test_endpoints(self):
        dif = Difusor()
        # porta livre
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); porta = s.getsockname()[1]; s.close()
        srv = criar_servidor(porta, dif, 0.2, True)
        parar = threading.Event()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=laco_amostragem, args=(simulador.Simulador(intervalo=0.2), dif, 0.2, parar), daemon=True).start()
        time.sleep(0.8)
        base = "http://127.0.0.1:%d" % porta
        try:
            self.assertEqual(json.load(urllib.request.urlopen(base + "/api/saude"))["ok"], True)
            cfg = json.load(urllib.request.urlopen(base + "/api/config"))
            self.assertIn("timbres", cfg)
            est = json.load(urllib.request.urlopen(base + "/api/estado"))
            self.assertIn("som", est)
            self.assertIn(b"netsound", urllib.request.urlopen(base + "/").read())
            r = urllib.request.urlopen(base + "/api/eventos", timeout=3)
            linhas = [r.readline() for _ in range(4)]
            self.assertTrue(any(l.startswith(b"data: ") for l in linhas))
            req = urllib.request.Request(base + "/api/estado", headers={"Host": "evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 403)
            self.assertEqual(srv.server_address[0], "127.0.0.1")
        finally:
            parar.set()
            srv.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
