# 🎧 netsound — Sonificação da rede

Transforma a atividade da rede do seu computador em **música**: você *ouve* o
download chegando, o SSH digitando, uma consulta DNS passando e um aparelho novo
entrando na rede.

* **Passivo e somente leitura**: só lê contadores que o próprio Linux já mantém
  (`/proc/net/dev`, `/proc/net/tcp*`, `/proc/net/udp*`, `/proc/net/arp`, e os comandos
  `ss -H` / `ip neigh` quando existem). **Não há varredura, nem captura de pacotes,
  nem leitura de conteúdo.** Não precisa de root.
* **Só biblioteca padrão do Python 3** (nenhum `pip install`).
* Duas saídas: **página ao vivo** (sintetizador Web Audio no navegador) e
  **arquivo `.wav`** (síntese em Python puro, com o módulo `wave`).

---

## Requisitos

| Item | Detalhe |
|---|---|
| Sistema | Linux (pensado para Fedora 44 — `python3` e `iproute` já vêm instalados) |
| Python | 3.8 ou superior (somente biblioteca padrão) |
| Navegador | Qualquer um moderno (Firefox, Chrome/Chromium) para o modo ao vivo |
| Opcionais | `ss` e `ip` (pacote `iproute`); se faltarem, o programa usa `/proc` |

Não é preciso instalar nada. Se quiser garantir:

```bash
sudo dnf install -y python3 iproute      # normalmente já instalados no Fedora
```

## Como usar no Fedora

```bash
unzip netsound.zip && cd netsound        # ou entre direto na pasta netsound/

# 1) Ao vivo no navegador
python3 netsound.py servir
#   abra  http://127.0.0.1:8765/  e clique em "▶ Iniciar áudio"
python3 netsound.py servir --demo        # mesma página, com tráfego SIMULADO

# 2) Gravar a rede real em .wav (amostra por N segundos e depois sintetiza)
python3 netsound.py gravar --segundos 60 --saida rede.wav

# 3) Gerar um .wav de demonstração (tráfego simulado; funciona sem rede)
python3 netsound.py demo --segundos 30 --saida rede-demo.wav
python3 netsound.py gravar --demo --segundos 30 --saida rede-demo.wav   # equivalente

# 4) Só ver os números no terminal (sem som)
python3 netsound.py monitorar --segundos 20
```

Para ouvir um `.wav` no Fedora: `pw-play rede.wav`, `paplay rede.wav`, `aplay rede.wav`
ou abra no player de sua preferência.

### Opções úteis

| Opção | Comandos | Efeito |
|---|---|---|
| `--porta 8765` | servir | porta HTTP local |
| `--abrir` | servir | abre o navegador sozinho |
| `--demo` | servir, gravar | usa tráfego simulado |
| `--intervalo 0.5` | todos | segundos entre amostras (mín. 0,1) |
| `--fonte auto\|ss\|proc` | servir, gravar, monitorar | de onde ler as conexões |
| `--interface enp0s4` | servir, gravar, monitorar | mede só essa interface (pode repetir) |
| `--incluir-loopback` | servir, gravar, monitorar | inclui conexões locais 127.0.0.1/::1 |
| `--taxa 22050` | gravar, demo | taxa do .wav (22050 ou 44100 Hz) |
| `--sem-reverb`, `--sem-cauda` | gravar, demo | sem reverb/eco; sem cauda final |

O `.wav` fica ~2,5 s mais longo que `--segundos` por causa da cauda do reverb
(use `--sem-cauda` para cortar). Arquivo estéreo, 16 bits: ≈ 88 KB por segundo em 22,05 kHz.

### Página ao vivo

* **▶ Iniciar áudio**: os navegadores só liberam som depois de um clique.
* **Volume** e **Reverb** (eco + reverb) ajustáveis em tempo real.
* **⏺ Gravar sessão (.wav)**: grava no navegador o que você está ouvindo e baixa o arquivo ao parar.
* Gráfico de taxas (últimos 60 s), barras por protocolo, dispositivos da LAN, espectro e lista de eventos.

## Como o mapeamento funciona

Escala: **pentatônica menor de Lá** (Lá Dó Ré Mi Sol) — qualquer combinação soa consonante.
Todos os timbres são somas de senoides com envelopes suaves (rampa de ataque, decaimento
exponencial, rampa final), então não há cliques.

| O que acontece na rede | Como soa |
|---|---|
| **Tráfego de entrada** (bytes/s) | Voz tipo *pad* no ouvido **esquerdo**. Quanto maior a taxa, mais **agudo** (graus da escala) e **mais alto**. Escala logarítmica: 100 B/s → 20 MB/s. |
| **Tráfego de saída** | Outra voz *pad* (timbre um pouco mais brilhante) no ouvido **direito**, deslocada uma quinta na escala em relação à entrada. |
| **HTTPS (443)** | Instrumento **sino**; pings agudos (~Lá5) a cada nova conexão + drone suave em Ré. |
| **HTTP (80)** | **Flauta** suave (~Lá4), mais à direita. |
| **DNS (53/853/5353)** | **Kalimba**, pings curtos e agudos. |
| **SSH (22)** | **Baixo** grave (~Lá2), no centro. |
| **Outros** | **Marimba** (~Lá3). |
| **Nova conexão** | *Ping* no timbre da classe; a nota (da pentatônica) vem de um hash do destino, então o mesmo destino sempre soa igual. Máx. 8 por amostra. |
| **Volume dos drones** | Cada classe tem um drone discreto cujo volume cresce com o log do nº de conexões abertas. |
| **Dispositivos na LAN** | Cada vizinho visto (por MAC) ganha uma **voz aguda** fixa (nota derivada do MAC, posição estéreo própria; até 8). Um dispositivo **novo** toca um **arpejo de carrilhão**. |
| **Pico de tráfego** | Se a taxa total passar de 3× a média móvel (e de 50 KB/s): **acorde** de pad (Am / Dsus, alternando) com intensidade proporcional ao pico + **bumbo** suave. Intervalo mínimo de 3 s. |
| **Fundo** | Uma tônica (Lá2) bem baixinha está sempre presente: a rede nunca é "silêncio total". |
| **Reverb e eco** | Eco estéreo (0,34 s) e reverb tipo Schroeder (≈2,6 s). |

O mapeamento é calculado **uma única vez**, em Python (`nucleo/mapeamento.py`), e tanto a
página quanto o gerador de `.wav` apenas o executam — por isso soam parecidos. Para mudar
notas, timbres, classes ou limiares, edite esse arquivo.

## Estrutura

```
netsound.py            CLI: servir | gravar | demo | monitorar
nucleo/coletor.py      leitura de /proc, ss, ip neigh (somente leitura)
nucleo/mapeamento.py   rede -> controles de som (escala, timbres, classes, picos)
nucleo/simulador.py    tráfego simulado (modo demo)
nucleo/sintese.py      sintetizador offline em Python puro -> .wav
nucleo/servidor.py     servidor HTTP em 127.0.0.1 (+ Server-Sent Events)
nucleo/web/            página (HTML/CSS/JS, Web Audio)
testes/                testes automáticos e verificador de .wav
exemplos/              rede-demo.wav, rede-real.wav, pagina-demo.png
```

## Privacidade e segurança

* **Nada sai da sua máquina**: o programa não faz nenhuma conexão de saída e não envia dados a ninguém.
* O servidor escuta **somente em `127.0.0.1`** (não é acessível por outros aparelhos da rede) e recusa
  requisições com cabeçalho `Host` que não seja local (proteção contra *DNS rebinding*).
* Usa apenas **leitura** de contadores e tabelas do sistema. Não captura pacotes, não vê conteúdo,
  não faz varredura/ping/ARP scan e não precisa de privilégios.
* A página mostra IPs de destino e MACs dos vizinhos **só na sua tela**; o `.wav` contém apenas som.
  Mesmo assim, o som e as listas revelam *padrões de uso* — pense nisso antes de compartilhar gravações.

## Limitações conhecidas

* A amostragem é periódica (padrão 0,5 s): conexões que abrem e fecham entre duas amostras não são vistas.
* O DNS comum usa UDP "não conectado" (e o Fedora usa o `systemd-resolved` em 127.0.0.53), então o
  instrumento DNS só aparece quando há sockets conectados na porta 53/853/5353 (ex.: DNS sobre TLS).
* Não há bytes por protocolo sem captura: a taxa é **por interface**; as classes usam o **nº de conexões**.
* A tabela ARP/vizinhos só lista aparelhos com quem o seu computador falou recentemente (é passiva de propósito).
* Interfaces `lo`, `docker*`, `veth*`, `br-*`, `virbr*` são ignoradas para não contar o tráfego em dobro;
  use `--interface` para escolher manualmente. Com VPN, a taxa pode contar o túnel e a interface física.

## Solução de problemas

| Problema | O que fazer |
|---|---|
| Sem som na página | Clique em **▶ Iniciar áudio**; verifique o volume da página, do sistema e da aba (não silenciada). Com `--demo` sempre há movimento. |
| `Address already in use` | Outra coisa usa a porta: `python3 netsound.py servir --porta 9000`. |
| Página diz "reconectando…" | O servidor foi encerrado; rode `servir` de novo e recarregue (F5). |
| Rede parada = pouco som | Normal: restam o fundo e os dispositivos. Gere tráfego (um download) ou teste com `--demo`. |
| Nenhum dispositivo aparece | A tabela ARP fica vazia se você não falou com ninguém na LAN. Veja `ip neigh`. |
| Interface errada / taxas zeradas | Veja os nomes em `ip -br link` e use `--interface wlp2s0` (por exemplo). |
| Sem `ss` | `--fonte proc` força `/proc`; instale com `sudo dnf install iproute` para usar `ss`. |
| Gravação demora | A síntese é Python puro (≈ 0,2 s por segundo de áudio a 22,05 kHz). |
| "Gravar sessão" não baixa | Permita downloads para 127.0.0.1; o navegador precisa suportar AudioWorklet (Firefox/Chrome atuais). |

## Testes

```bash
python3 testes/testar.py                        # testes unitários (stdlib)
python3 testes/verificar_wav.py exemplos/*.wav  # duração, silêncio, clipping, estatísticas
# página no Chrome headless (precisa de node + puppeteer-core), com o servidor rodando:
NODE_PATH=/caminho/node_modules node testes/pagina.js http://127.0.0.1:8765/
```

Licença: uso livre para fins pessoais e educacionais.
