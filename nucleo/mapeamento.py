"""Mapeamento rede -> som.

Este módulo é a ÚNICA fonte de verdade do mapeamento. Ele transforma "quadros"
(medições da rede) em "controles de som" (vozes contínuas + eventos). Tanto o
sintetizador do navegador (Web Audio) quanto o renderizador de .wav em Python
apenas executam esses controles, então o resultado é o mesmo nos dois.
"""
import math
import zlib

# Escala pentatônica menor de Lá (A C D E G), em semitons a partir da tônica.
ESCALA = [0, 3, 5, 7, 10]
NOMES_NOTAS = ["Dó", "Dó#", "Ré", "Ré#", "Mi", "Fá", "Fá#", "Sol", "Sol#", "Lá", "Lá#", "Si"]


def midi_para_freq(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def nota_pent(indice, base_midi):
    """Nota (Hz) do grau `indice` da pentatônica a partir de `base_midi`."""
    oitava, grau = divmod(int(indice), 5)
    return midi_para_freq(base_midi + 12 * oitava + ESCALA[grau])


def nome_nota(freq):
    if freq <= 0:
        return "-"
    m = int(round(69 + 12 * math.log2(freq / 440.0)))
    return "%s%d" % (NOMES_NOTAS[m % 12], m // 12 - 1)


def _norm(parciais):
    total = sum(a for _, a in parciais)
    return [[m, round(a / total, 4)] for m, a in parciais]


# Timbres: lista de parciais [multiplicador de frequência, amplitude relativa],
# tempo de ataque (s) e tempo de decaimento até -60 dB (s). Só senoides: som suave.
TIMBRES = {
    "pad_entrada": {"parciais": _norm([[1, 1.0], [1.005, 0.7], [2, 0.18]]), "ataque": 0.0, "decai": 0.0},
    "pad_saida":   {"parciais": _norm([[1, 1.0], [0.995, 0.6], [2, 0.30], [3, 0.10]]), "ataque": 0.0, "decai": 0.0},
    "drone":       {"parciais": _norm([[1, 1.0], [2, 0.15]]), "ataque": 0.0, "decai": 0.0},
    "dispositivo": {"parciais": _norm([[1, 1.0], [2.001, 0.12]]), "ataque": 0.0, "decai": 0.0},
    "sino":     {"parciais": _norm([[1, 1.0], [2.76, 0.28], [5.4, 0.10]]), "ataque": 0.004, "decai": 1.8},
    "flauta":   {"parciais": _norm([[1, 1.0], [2, 0.16], [3, 0.05]]), "ataque": 0.035, "decai": 1.0},
    "kalimba":  {"parciais": _norm([[1, 1.0], [4.0, 0.20], [9.2, 0.05]]), "ataque": 0.002, "decai": 0.6},
    "baixo":    {"parciais": _norm([[1, 1.0], [2, 0.35], [3, 0.08]]), "ataque": 0.015, "decai": 1.3},
    "marimba":  {"parciais": _norm([[1, 1.0], [4.0, 0.25], [10.0, 0.04]]), "ataque": 0.003, "decai": 0.55},
    "carrilhao": {"parciais": _norm([[1, 1.0], [3, 0.18], [6, 0.06]]), "ataque": 0.004, "decai": 1.6},
    "acorde":   {"parciais": _norm([[1, 1.0], [1.003, 0.5], [2, 0.22]]), "ataque": 0.35, "decai": 3.5},
}

# Classes de protocolo/porta. `base_midi`: nota base dos pings (A2=45, A3=57...);
# `drone_midi`: nota do drone suave quando há conexões da classe.
CLASSES = {
    "https":  {"rotulo": "HTTPS (443)", "portas": [443, 8443], "timbre": "sino",
               "base_midi": 81, "drone_midi": 62, "pan": -0.30, "cor": "#4cc9f0"},
    "http":   {"rotulo": "HTTP (80)", "portas": [80, 8080], "timbre": "flauta",
               "base_midi": 69, "drone_midi": 52, "pan": 0.40, "cor": "#f9c74f"},
    "dns":    {"rotulo": "DNS (53)", "portas": [53, 853, 5353], "timbre": "kalimba",
               "base_midi": 81, "drone_midi": 72, "pan": 0.30, "cor": "#90be6d"},
    "ssh":    {"rotulo": "SSH (22)", "portas": [22], "timbre": "baixo",
               "base_midi": 45, "drone_midi": 45, "pan": 0.0, "cor": "#f94144"},
    "outros": {"rotulo": "Outros", "portas": [], "timbre": "marimba",
               "base_midi": 57, "drone_midi": 67, "pan": -0.10, "cor": "#b794f4"},
}
ORDEM_CLASSES = ["https", "http", "dns", "ssh", "outros"]

PORTA_PARA_CLASSE = {}
for _nome, _c in CLASSES.items():
    for _p in _c["portas"]:
        PORTA_PARA_CLASSE[_p] = _nome


def classificar(porta_local, porta_remota):
    """Classe do protocolo pela porta remota (ou local, se for um servidor)."""
    for p in (porta_remota, porta_local):
        if p in PORTA_PARA_CLASSE:
            return PORTA_PARA_CLASSE[p]
    return "outros"


CONFIG = {
    "versao": 1,
    "timbres": TIMBRES,
    "classes": CLASSES,
    "ordem_classes": ORDEM_CLASSES,
    "ganho_evento": 0.22,        # ganho base dos eventos (multiplica 'vel')
    "suavizacao": {"ganho": 0.25, "freq": 0.12},   # constantes de tempo (s)
    "max_dispositivos": 8,
    "reverb": {"mix": 0.28, "duracao": 2.6},
    "eco": {"tempo": 0.34, "realimentacao": 0.28, "mix": 0.16},
}

ACORDES = [
    [45, 52, 57, 60, 64, 67],   # Lá2 Mi3 Lá3 Dó4 Mi4 Sol4 (Am)
    [50, 57, 62, 64, 67, 69],   # Ré3 Lá3 Ré4 Mi4 Sol4 Lá4 (Dsus)
]


def nivel_taxa(bps):
    """Taxa (bytes/s) -> nível 0..1 em escala logarítmica (100 B/s .. 20 MB/s)."""
    if bps < 100:
        return 0.0
    return min(1.0, math.log10(bps / 100.0) / math.log10(2e5))


def _h(texto):
    return zlib.crc32(texto.encode("utf-8", "replace")) & 0xFFFFFFFF


class Mapeador:
    """Mantém o estado (suavização, histerese, detecção de picos)."""

    N_GRAUS = 12

    def __init__(self):
        self.nivel_in = 0.0
        self.nivel_out = 0.0
        self.grau_in = 0.0
        self.grau_out = 0.0
        self.media_lenta = None
        self.ultimo_pico = -1e9
        self.n_acorde = 0
        self.ordem_disp = []

    def _grau(self, anterior, nivel):
        alvo = nivel * (self.N_GRAUS - 1)
        # histerese: só muda de nota se o alvo se afastar o bastante
        if abs(alvo - anterior) > 0.7:
            return float(round(alvo))
        return anterior

    def processar(self, q):
        dt = max(0.05, float(q.get("dt", 0.5)))
        t = float(q.get("t", 0.0))
        a = 1.0 - math.exp(-dt / 0.6)
        self.nivel_in += a * (nivel_taxa(q["entrada_bps"]) - self.nivel_in)
        self.nivel_out += a * (nivel_taxa(q["saida_bps"]) - self.nivel_out)
        self.grau_in = self._grau(self.grau_in, self.nivel_in)
        self.grau_out = self._grau(self.grau_out, self.nivel_out)

        vozes = []
        # Fundo: tônica bem suave, sempre presente (a rede nunca é "silêncio total")
        vozes.append({"id": "fundo", "timbre": "drone", "freq": midi_para_freq(45),
                      "ganho": 0.03, "pan": 0.0})
        # Entrada à esquerda, saída à direita.
        f_in = nota_pent(int(self.grau_in), 57)
        f_out = nota_pent(int(self.grau_out) + 3, 57)
        g_in = 0.0 if q["entrada_bps"] < 100 else 0.05 + 0.26 * self.nivel_in ** 1.2
        g_out = 0.0 if q["saida_bps"] < 100 else 0.05 + 0.26 * self.nivel_out ** 1.2
        vozes.append({"id": "entrada", "timbre": "pad_entrada", "freq": f_in, "ganho": round(g_in, 4), "pan": -0.7})
        vozes.append({"id": "saida", "timbre": "pad_saida", "freq": f_out, "ganho": round(g_out, 4), "pan": 0.7})

        # Drones por classe de protocolo (volume ~ log do nº de conexões).
        for nome in ORDEM_CLASSES:
            n = q["conexoes"]["por_classe"].get(nome, 0)
            g = 0.0 if n <= 0 else 0.07 * min(1.0, math.log2(1 + n) / 5.0)
            c = CLASSES[nome]
            vozes.append({"id": "classe_" + nome, "timbre": "drone",
                          "freq": midi_para_freq(c["drone_midi"]), "ganho": round(g, 4), "pan": c["pan"]})

        # Dispositivos da LAN: cada um uma voz aguda e discreta.
        ids_atuais = [d["id"] for d in q.get("vizinhos", [])]
        for i in ids_atuais:
            if i not in self.ordem_disp:
                self.ordem_disp.append(i)
        ativos = [i for i in self.ordem_disp if i in ids_atuais][: CONFIG["max_dispositivos"]]
        for i in ativos:
            h = _h(i)
            freq = midi_para_freq(69 + 12 * ((h >> 8) % 2) + ESCALA[h % 5])
            pan = ((h >> 4) % 9 - 4) / 5.0
            vozes.append({"id": "disp_" + i, "timbre": "dispositivo", "freq": freq,
                          "ganho": 0.016, "pan": pan})

        eventos = []
        # Novas conexões -> pings no timbre da classe.
        novas = q.get("novas", [])
        escolhidas = novas[:8]
        for k, c in enumerate(escolhidas):
            cl = CLASSES[c["classe"]]
            h = _h("%s:%s" % (c.get("remoto", ""), c.get("porta", 0)))
            oitava_extra = 12 * ((h >> 8) % 2) if cl["base_midi"] < 70 else 0
            freq = midi_para_freq(cl["base_midi"] + oitava_extra + ESCALA[h % 5])
            vel = 0.35 + 0.3 * ((h >> 12) & 3) / 3.0
            eventos.append({"tipo": "nota", "timbre": cl["timbre"], "freq": round(freq, 2),
                            "vel": round(vel, 3), "atraso": round((k + 0.5) / len(escolhidas) * dt * 0.9, 3),
                            "pan": cl["pan"]})
        # Novo dispositivo -> arpejo de carrilhão.
        for j, mac in enumerate(q.get("novos_vizinhos", [])[:2]):
            h = _h(mac)
            idx = h % 5
            for n in range(3):
                f = nota_pent(idx + n, 69)
                eventos.append({"tipo": "nota", "timbre": "carrilhao", "freq": round(f, 2),
                                "vel": 0.5, "atraso": round(0.12 * n + 0.4 * j, 3), "pan": 0.0})

        # Pico de tráfego -> acorde mais intenso + bumbo suave.
        total = q["entrada_bps"] + q["saida_bps"]
        pico = None
        if self.media_lenta is None:
            self.media_lenta = total
        if total > max(3.0 * self.media_lenta, 50000.0) and t - self.ultimo_pico > 3.0:
            r = total / max(self.media_lenta, 1.0)
            intens = min(1.0, 0.4 + 0.12 * math.log2(r))
            self.ultimo_pico = t
            pico = round(intens, 3)
            acorde = ACORDES[self.n_acorde % len(ACORDES)]
            self.n_acorde += 1
            for k, m in enumerate(acorde):
                eventos.append({"tipo": "nota", "timbre": "acorde", "freq": round(midi_para_freq(m), 2),
                                "vel": round(intens * (0.9 - 0.05 * k), 3), "atraso": round(0.03 * k, 3),
                                "pan": round(-0.5 + k * 0.2, 2)})
            eventos.append({"tipo": "bumbo", "vel": round(0.5 + 0.5 * intens, 3), "atraso": 0.0, "pan": 0.0})
        self.media_lenta += (1.0 - math.exp(-dt / 15.0)) * (total - self.media_lenta)

        return {
            "vozes": vozes,
            "eventos": eventos,
            "info": {"nota_entrada": nome_nota(f_in), "nota_saida": nome_nota(f_out),
                     "nivel_entrada": round(self.nivel_in, 3), "nivel_saida": round(self.nivel_out, 3),
                     "pico": pico},
        }
