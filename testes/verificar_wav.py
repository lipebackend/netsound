#!/usr/bin/env python3
"""Verifica um .wav: duração, silêncio, clipping e estatísticas de amostras.
Uso: python3 testes/verificar_wav.py arquivo.wav [...]"""
import array
import math
import sys
import wave


def analisar(caminho):
    with wave.open(caminho, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        dados = array.array("h")
        dados.frombytes(w.readframes(n))
    assert sw == 2, "esperava 16 bits"
    canais = [dados[i::ch] for i in range(ch)]
    res = {"arquivo": caminho, "canais": ch, "taxa": sr, "duracao_s": round(n / sr, 2)}
    for i, c in enumerate(canais):
        pico = max(max(c), -min(c))
        rms = math.sqrt(sum(x * x for x in c) / len(c))
        no_teto = sum(1 for x in c if abs(x) >= 32767)
        res["canal%d" % i] = {"pico": round(pico / 32768, 3), "rms": round(rms / 32768, 4),
                              "rms_dbfs": round(20 * math.log10(max(rms, 1) / 32768), 1),
                              "media_dc": round(sum(c) / len(c) / 32768, 5), "amostras_no_teto": no_teto}
    # janelas de 0,5 s: quantas têm som?
    jan = int(sr * 0.5)
    c0 = canais[0]
    rmss = [math.sqrt(sum(x * x for x in c0[i:i + jan]) / max(1, len(c0[i:i + jan]))) / 32768 for i in range(0, len(c0), jan)]
    res["janelas_0.5s"] = len(rmss)
    res["janelas_silenciosas(<-70dBFS)"] = sum(1 for r in rmss if r < 10 ** (-70 / 20))
    res["rms_janelas_min_max"] = [round(min(rmss), 4), round(max(rmss), 4)]
    # maior salto entre amostras consecutivas (detecção grosseira de cliques)
    salto = max(abs(c0[i + 1] - c0[i]) for i in range(len(c0) - 1)) / 32768
    res["maior_salto_entre_amostras"] = round(salto, 3)
    if ch == 2:
        res["diferenca_L_R(estereo)"] = round(sum(abs(a - b) for a, b in zip(canais[0], canais[1])) / len(c0) / 32768, 4)
    return res


if __name__ == "__main__":
    import json
    ok = True
    for arq in sys.argv[1:]:
        r = analisar(arq)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        for k in ("canal0", "canal1"):
            if k in r:
                ok &= r[k]["pico"] > 0.05 and r[k]["amostras_no_teto"] == 0 and r[k]["rms"] > 0.003
    print("RESULTADO:", "OK (não silencioso, sem clipping)" if ok else "FALHA")
    sys.exit(0 if ok else 1)
