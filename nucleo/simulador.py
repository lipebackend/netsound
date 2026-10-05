"""Tráfego SIMULADO (modo demo): gera quadros no mesmo formato do coletor real.

Não toca na rede. Útil para testar o som em máquinas sem tráfego (sandbox).
O roteiro dura 36 s e se repete; os dispositivos aparecem aos poucos.
"""
import math
import random

from .mapeamento import ORDEM_CLASSES

CICLO = 36.0
DISPOSITIVOS = [(3.0, "aa:bb:cc:00:00:01", "192.168.0.1"),
                (8.0, "aa:bb:cc:00:00:02", "192.168.0.23"),
                (14.0, "aa:bb:cc:00:00:03", "192.168.0.42"),
                (21.0, "aa:bb:cc:00:00:04", "192.168.0.77")]


class Simulador:
    def __init__(self, semente=7, intervalo=0.5):
        self.rng = random.Random(semente)
        self.dt = intervalo
        self.t = 0.0
        self.conexoes = {c: [] for c in ORDEM_CLASSES}
        self.contador = 0
        self.vistos = set()
        self.primeiro = True
        self.media_in = 15000.0
        self.media_out = 4000.0

    def _perfil(self, tc):
        """(taxa_in, taxa_out, {classe: chegadas/s}, alvo de conexões por classe)."""
        base = {"https": 0.6, "dns": 0.8, "http": 0.05, "outros": 0.1, "ssh": 0.0}
        alvo = {"https": 5, "dns": 1, "http": 0, "outros": 2, "ssh": 0}
        ent, sai = 15e3, 4e3
        if 6 <= tc < 9:      # navegação leve
            ent, sai = 90e3, 15e3; base["https"] = 2.5; base["dns"] = 2.0; alvo["https"] = 9
        if 10 <= tc < 17:    # download grande
            k = 1 - abs((tc - 13.5) / 3.5) * 0.35
            ent, sai = 7.5e6 * k, 300e3 * k; base["https"] = 1.0; alvo["https"] = 8; alvo["outros"] = 4
        if 18 <= tc < 23:    # sessão SSH com "digitação"
            teclas = 1 if self.rng.random() < 0.6 else 0
            ent, sai = 2e3 + 6e3 * teclas, 1.5e3 + 5e3 * teclas
            base["ssh"] = 0.5; alvo["ssh"] = 2
        if 23.5 <= tc < 28:  # vídeo (QUIC/HTTPS)
            ent, sai = 3.2e6, 80e3; base["https"] = 3.0; base["dns"] = 1.5; alvo["https"] = 14; alvo["outros"] = 5
        if 28 <= tc < 29:
            ent, sai = 900e3, 40e3; base["http"] = 3.0; alvo["http"] = 3
        if 29 <= tc:
            ent, sai = 20e3, 6e3
        return ent, sai, base, alvo

    def proximo(self):
        dt = self.dt
        self.t += dt
        tc = self.t % CICLO
        ent_alvo, sai_alvo, chegadas, alvo = self._perfil(tc)
        r = self.rng
        # suaviza e acrescenta ruído log-normal
        self.media_in += (ent_alvo - self.media_in) * 0.55
        self.media_out += (sai_alvo - self.media_out) * 0.55
        ent = self.media_in * math.exp(r.gauss(0, 0.25))
        sai = self.media_out * math.exp(r.gauss(0, 0.25))

        novas, fechadas = [], 0
        for cl in ORDEM_CLASSES:
            lista = self.conexoes[cl]
            n_novas = 0
            lam = chegadas.get(cl, 0) * dt
            while r.random() < lam / (1 + lam) * 1.3 and n_novas < 6:
                n_novas += 1
                lam *= 0.5
            for _ in range(n_novas):
                self.contador += 1
                porta = {"https": 443, "http": 80, "dns": 53, "ssh": 22}.get(cl, r.choice([5222, 8883, 3478, 9000, 1935]))
                remoto = "%d.%d.%d.%d" % (r.choice([142, 151, 104, 172, 52, 34]), r.randrange(256), r.randrange(256), r.randrange(1, 255))
                lista.append(self.contador)
                novas.append({"classe": cl, "proto": "udp" if cl == "dns" else "tcp", "remoto": remoto, "porta": porta})
            # fecha/ajusta para o alvo
            while len(lista) > alvo[cl] + 1 or (lista and r.random() < 0.08 and len(lista) > alvo[cl]):
                lista.pop(r.randrange(len(lista)))
                fechadas += 1
        por_classe = {c: len(self.conexoes[c]) for c in ORDEM_CLASSES}

        vizinhos = [{"id": m, "ip": ip, "mac": m, "iface": "eth0"} for (ta, m, ip) in DISPOSITIVOS if self.t >= ta]
        ids = {v["id"] for v in vizinhos}
        novos = sorted(ids - self.vistos) if not self.primeiro or self.t > 1 else []
        self.vistos |= ids
        self.primeiro = False
        # em ciclos seguintes a rede "reinicia" os pings, mas os dispositivos permanecem
        return {
            "t": round(self.t, 3), "dt": dt, "demo": True,
            "entrada_bps": round(ent, 1), "saida_bps": round(sai, 1),
            "entrada_pps": round(ent / 900, 1), "saida_pps": round(sai / 700, 1),
            "interfaces": {"eth0": {"entrada_bps": round(ent, 1), "saida_bps": round(sai, 1)}},
            "conexoes": {"total": sum(por_classe.values()), "por_classe": por_classe,
                         "remotos": sum(por_classe.values())},
            "novas": novas, "fechadas": fechadas,
            "vizinhos": vizinhos, "novos_vizinhos": novos,
            "fonte": "simulado",
        }
