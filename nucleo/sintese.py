"""Sintetizador offline em Python puro (stdlib): controles de som -> .wav estéreo.

Executa exatamente os mesmos "controles" (vozes contínuas + eventos) que o
sintetizador Web Audio da página, usando as mesmas receitas de timbre.
"""
import array
import itertools
import math
import wave

from .mapeamento import CONFIG, TIMBRES

DOIS_PI = 2.0 * math.pi
GANHO_EVENTO = CONFIG["ganho_evento"]


def ganhos_pan(pan):
    """Panorama de potência constante: pan -1 (esq.) .. +1 (dir.)."""
    a = (max(-1.0, min(1.0, pan)) + 1.0) * math.pi / 4.0
    return math.cos(a), math.sin(a)


class _Voz:
    def __init__(self, timbre, freq, pan):
        self.parciais = TIMBRES[timbre]["parciais"]
        self.fase = [0.0] * len(self.parciais)
        self.f = freq
        self.g = 0.0
        self.alvo_f = freq
        self.alvo_g = 0.0
        self.pan = pan


class Renderizador:
    def __init__(self, taxa=22050, reverb=True, eco=True, cauda=2.5):
        self.sr = taxa
        self.reverb = reverb
        self.eco = eco
        self.cauda = cauda
        self._cache_notas = {}
        self._cache_pot = {}
        self._bumbo = None

    # ------------------------------------------------------------ utilitários
    def _potencias(self, tau, n):
        chave = (tau, n)
        p = self._cache_pot.get(chave)
        if p is None:
            r = math.exp(-1.0 / (tau * self.sr))
            p = [r ** (i + 1) for i in range(n)]
            self._cache_pot[chave] = p
        return p

    def _nota(self, timbre, freq):
        """Forma de onda (pico <= 1) de uma nota com vel=1; guardada em cache."""
        chave = (timbre, round(freq, 2))
        buf = self._cache_notas.get(chave)
        if buf is not None:
            return buf
        sr = self.sr
        T = TIMBRES[timbre]
        atk = max(T["ataque"], 0.002)
        saidas = []
        for k, (m, a) in enumerate(T["parciais"]):
            f = freq * m
            if f >= sr * 0.45:
                continue
            dec = T["decai"] / (1.0 + 0.5 * k)      # parciais agudos morrem antes
            na = int(atk * sr)
            nd = int(dec * sr)
            r = 10.0 ** (-3.0 / (dec * sr))          # -60 dB em `dec` segundos
            env = [i / na for i in range(na)] + [r ** j for j in range(nd)]
            nf = min(len(env), int(0.01 * sr))       # rampa final: sem clique
            for j in range(nf):
                env[len(env) - 1 - j] *= j / nf
            w = DOIS_PI * f / sr
            saidas.append([a * e * math.sin(w * i) for i, e in enumerate(env)])
        n = max(len(s) for s in saidas)
        buf = [0.0] * n
        for s in saidas:
            for i, x in enumerate(s):
                buf[i] += x
        self._cache_notas[chave] = buf
        return buf

    def _buf_bumbo(self):
        if self._bumbo is None:
            sr = self.sr
            n = int(0.5 * sr)
            fase = 0.0
            out = []
            r = 10.0 ** (-3.0 / (0.45 * sr))
            for i in range(n):
                t = i / sr
                f = 130.0 * (45.0 / 130.0) ** (min(t, 0.25) / 0.25)
                fase += DOIS_PI * f / sr
                env = (i / (0.004 * sr)) if i < 0.004 * sr else r ** (i - 0.004 * sr)
                out.append(math.sin(fase) * min(env, 1.0))
            nf = int(0.01 * sr)
            for j in range(nf):
                out[n - 1 - j] *= j / nf
            self._bumbo = out
        return self._bumbo

    # ----------------------------------------------------------------- núcleo
    def renderizar(self, sons, dts):
        """sons: lista de controles (Mapeador.processar); dts: duração de cada um (s)."""
        sr = self.sr
        if self.cauda > 0:  # cauda: vozes se apagam suavemente e as notas/reverb terminam de soar
            k = max(1, int(round(self.cauda / 0.5)))
            sons = list(sons) + [{"vozes": [], "eventos": []}] * k
            dts = list(dts) + [0.5] * k
        total_amostras = int(round(sum(dts) * sr))
        N = total_amostras
        L = [0.0] * N
        R = [0.0] * N
        vozes = {}
        tau_g = CONFIG["suavizacao"]["ganho"]
        tau_f = CONFIG["suavizacao"]["freq"]
        acum = 0.0
        ini = 0
        for som, dt in zip(sons, dts):
            acum += dt
            fim = int(round(acum * sr))
            n = fim - ini
            if n <= 0:
                continue
            presentes = set()
            for v in som["vozes"]:
                presentes.add(v["id"])
                st = vozes.get(v["id"])
                if st is None:
                    if v["ganho"] <= 0.0005:
                        continue
                    st = vozes[v["id"]] = _Voz(v["timbre"], v["freq"], v["pan"])
                st.alvo_f, st.alvo_g, st.pan = v["freq"], v["ganho"], v["pan"]
            for vid, st in vozes.items():
                if vid not in presentes:
                    st.alvo_g = 0.0
            pg = self._potencias(tau_g, n)
            pf = self._potencias(tau_f, n)
            for vid in list(vozes):
                st = vozes[vid]
                if st.alvo_g == 0.0 and st.g < 1e-5:
                    st.g = 0.0
                    st.f = st.alvo_f
                    if vid not in presentes:
                        del vozes[vid]
                    continue
                dg = st.g - st.alvo_g
                df = st.f - st.alvo_f
                gs = [st.alvo_g + dg * p for p in pg]
                ws = [(st.alvo_f + df * p) * DOIS_PI / sr for p in pf]
                st.g, st.f = gs[-1], st.alvo_f + df * pf[-1]
                buf = [0.0] * n
                for j, (m, a) in enumerate(st.parciais):
                    fases = list(itertools.accumulate((w * m for w in ws), initial=st.fase[j]))
                    st.fase[j] = fases[-1] % DOIS_PI
                    buf = [b + a * math.sin(p) for b, p in zip(buf, fases[1:])]
                gl, gr = ganhos_pan(st.pan)
                L[ini:fim] = [l + b * g * gl for l, b, g in zip(L[ini:fim], buf, gs)]
                R[ini:fim] = [r + b * g * gr for r, b, g in zip(R[ini:fim], buf, gs)]
            for ev in som["eventos"]:
                if ev["tipo"] == "nota":
                    buf = self._nota(ev["timbre"], ev["freq"])
                    amp = ev["vel"] * GANHO_EVENTO
                else:
                    buf = self._buf_bumbo()
                    amp = ev["vel"] * GANHO_EVENTO * 1.6
                ini_ev = ini + int(ev.get("atraso", 0.0) * sr)
                fim_ev = min(N, ini_ev + len(buf))
                if fim_ev <= ini_ev:
                    continue
                gl, gr = ganhos_pan(ev.get("pan", 0.0))
                seg = buf[: fim_ev - ini_ev]
                L[ini_ev:fim_ev] = [l + b * amp * gl for l, b in zip(L[ini_ev:fim_ev], seg)]
                R[ini_ev:fim_ev] = [r + b * amp * gr for r, b in zip(R[ini_ev:fim_ev], seg)]
            ini = fim

        if self.eco:
            L, R = self._eco(L, R)
        if self.reverb:
            L, R = self._reverb(L, R)
        return self._finalizar(L, R, total_amostras)

    # ---------------------------------------------------------------- efeitos
    def _eco(self, L, R):
        sr = self.sr
        cfg = CONFIG["eco"]
        d = int(cfg["tempo"] * sr)
        fb = cfg["realimentacao"]
        N = len(L)
        m = [(a + b) * 0.5 for a, b in zip(L, R)]
        e = [0.0] * N
        for s in range(d, N, d):
            fim = min(N, s + d)
            e[s:fim] = [x + fb * y for x, y in zip(m[s - d:fim - d], e[s - d:fim - d])]
        mix = cfg["mix"]
        meio = d // 2
        L = [l + mix * x for l, x in zip(L, e)]
        eR = [0.0] * meio + e[: N - meio]
        R = [r + mix * x for r, x in zip(R, eR)]
        return L, R

    @staticmethod
    def _comb(x, d, fb):
        N = len(x)
        y = list(x[: min(d, N)]) + [0.0] * (N - min(d, N))
        for s in range(d, N, d):
            fim = min(N, s + d)
            y[s:fim] = [a + fb * b for a, b in zip(x[s:fim], y[s - d:fim - d])]
        return y

    @staticmethod
    def _allpass(x, d, g):
        N = len(x)
        y = [-g * v for v in x[: min(d, N)]] + [0.0] * (N - min(d, N))
        for s in range(d, N, d):
            fim = min(N, s + d)
            y[s:fim] = [-g * a + b + g * c for a, b, c in zip(x[s:fim], x[s - d:fim - d], y[s - d:fim - d])]
        return y

    def _reverb(self, L, R):
        sr = self.sr
        esc = sr / 44100.0
        rt60 = CONFIG["reverb"]["duracao"]
        mix = CONFIG["reverb"]["mix"]
        mono = [(a + b) * 0.5 for a, b in zip(L, R)]
        saidas = []
        for atrasos, aps in (((1116, 1188, 1277, 1356), (556, 441)), ((1139, 1211, 1300, 1379), (579, 464))):
            soma = [0.0] * len(mono)
            for d0 in atrasos:
                d = max(8, int(d0 * esc))
                fb = 10.0 ** (-3.0 * d / (rt60 * sr))
                c = self._comb(mono, d, fb)
                k = (1.0 - fb) * 0.9
                soma = [s + k * v for s, v in zip(soma, c)]
            for d0 in aps:
                soma = self._allpass(soma, max(8, int(d0 * esc)), 0.5)
            a = 1.0 - math.exp(-DOIS_PI * 4500.0 / sr)     # passa-baixa suave
            soma = list(itertools.accumulate(soma, lambda y, x: y + a * (x - y)))
            saidas.append(soma)
        wl, wr = saidas
        L = [l + mix * w for l, w in zip(L, wl)]
        R = [r + mix * w for r, w in zip(R, wr)]
        return L, R

    # ------------------------------------------------------------------ saída
    def _finalizar(self, L, R, total):
        sr = self.sr
        N = len(L)
        # entrada/saída suaves (sem clique) e fade final na cauda
        fi = int(0.05 * sr)
        for i in range(min(fi, N)):
            g = i / fi
            L[i] *= g
            R[i] *= g
        ff = min(N, int(0.5 * sr))
        for j in range(ff):
            g = j / ff
            L[N - 1 - j] *= g
            R[N - 1 - j] *= g
        # limitador suave (tanh) e normalização para -1 dBFS
        th = math.tanh
        L = [th(1.2 * x) for x in L]
        R = [th(1.2 * x) for x in R]
        pico = max(max(map(abs, L), default=0.0), max(map(abs, R), default=0.0))
        alvo = 0.89
        k = alvo / pico if pico > 1e-6 else 1.0
        inter = array.array("h", bytes(4 * N))
        inter[0::2] = array.array("h", [int(round(x * k * 32767)) for x in L])
        inter[1::2] = array.array("h", [int(round(x * k * 32767)) for x in R])
        return inter


def escrever_wav(caminho, inter, taxa):
    with wave.open(caminho, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(taxa)
        data = inter
        if array.array("H", [1]).tobytes()[0] != 1:  # máquina big-endian
            data = array.array("h", inter)
            data.byteswap()
        w.writeframes(data.tobytes())
