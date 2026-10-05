// netsound — página ao vivo: recebe quadros do servidor local e toca com Web Audio.
// O mapeamento rede -> som é calculado no servidor (Python); aqui só executamos os
// "controles" (vozes contínuas + eventos) com um sintetizador aditivo suave.
'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const estado = {
    cfg: null, sintese: null, ultimo: null, quadros: 0, erros: [], hist: [],
    ultimaMsg: 0, log: [], ping: {}, gravacao: null, ultimaGravacao: null,
  };
  window.__netsound = estado; // gancho para testes automatizados
  window.addEventListener('error', (e) => estado.erros.push(String(e.message)));
  window.addEventListener('unhandledrejection', (e) => estado.erros.push('promessa: ' + e.reason));

  const fmtTaxa = (b) => {
    const un = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
    let i = 0;
    while (b >= 1024 && i < 3) { b /= 1024; i++; }
    return b.toFixed(i ? 1 : 0) + ' ' + un[i];
  };
  const hora = () => new Date().toLocaleTimeString('pt-BR');

  // ------------------------------------------------------------ sintetizador
  class Sintese {
    constructor(cfg) {
      this.cfg = cfg;
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = (this.ctx = new Ctx({ latencyHint: 'interactive' }));
      this.vozes = new Map();
      this.pendentes = 0;

      this.mestre = ctx.createGain();
      this.comp = ctx.createDynamicsCompressor();
      this.comp.threshold.value = -14; this.comp.knee.value = 24;
      this.comp.ratio.value = 4; this.comp.attack.value = 0.01; this.comp.release.value = 0.3;
      this.analisador = ctx.createAnalyser();
      this.analisador.fftSize = 1024; this.analisador.smoothingTimeConstant = 0.8;
      this.mestre.connect(this.comp);
      this.comp.connect(this.analisador);
      this.comp.connect(ctx.destination);

      // barramento de entrada: seco + eco + reverb
      this.barra = ctx.createGain();
      this.barra.connect(this.mestre);
      this.efeitos = ctx.createGain();           // quantidade de efeitos (0..1)
      this.efeitos.gain.value = 1;
      this.barra.connect(this.efeitos);
      const eco = ctx.createDelay(1.5);
      eco.delayTime.value = cfg.eco.tempo;
      const fb = ctx.createGain(); fb.gain.value = cfg.eco.realimentacao;
      const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 3000;
      const ecoMix = ctx.createGain(); ecoMix.gain.value = cfg.eco.mix;
      this.efeitos.connect(eco); eco.connect(lp); lp.connect(fb); fb.connect(eco);
      lp.connect(ecoMix); ecoMix.connect(this.mestre);
      const conv = ctx.createConvolver();
      conv.buffer = this.respostaImpulso(cfg.reverb.duracao);
      const revMix = ctx.createGain(); revMix.gain.value = cfg.reverb.mix * 0.8;
      this.efeitos.connect(conv); conv.connect(revMix); revMix.connect(this.mestre);
    }

    respostaImpulso(dur) {
      const sr = this.ctx.sampleRate, n = Math.floor(dur * sr);
      const buf = this.ctx.createBuffer(2, n, sr);
      for (let c = 0; c < 2; c++) {
        const d = buf.getChannelData(c);
        let y = 0;
        for (let i = 0; i < n; i++) {
          y += 0.45 * ((Math.random() * 2 - 1) - y);          // ruído filtrado (som mais "escuro")
          d[i] = y * Math.pow(10, -3 * i / n) * Math.min(1, i / (0.01 * sr));
        }
      }
      return buf;
    }

    volume(v) { this.mestre.gain.setTargetAtTime(v * 2, this.ctx.currentTime, 0.05); }
    quantidadeEfeitos(v) { this.efeitos.gain.setTargetAtTime(v, this.ctx.currentTime, 0.1); }

    criarVoz(v) {
      const ctx = this.ctx, T = this.cfg.timbres[v.timbre];
      const g = ctx.createGain(); g.gain.value = 0;
      const p = ctx.createStereoPanner(); p.pan.value = v.pan;
      g.connect(p); p.connect(this.barra);
      const oscs = T.parciais.map(([m, a]) => {
        const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = v.freq * m;
        const og = ctx.createGain(); og.gain.value = a;
        o.connect(og); og.connect(g); o.start();
        return { o, m };
      });
      return { g, p, oscs, saiu: null };
    }

    atualizarVozes(lista) {
      const ctx = this.ctx, t = ctx.currentTime, sg = this.cfg.suavizacao.ganho, sf = this.cfg.suavizacao.freq;
      const presentes = new Set();
      for (const v of lista) {
        presentes.add(v.id);
        let voz = this.vozes.get(v.id);
        if (!voz) { voz = this.criarVoz(v); this.vozes.set(v.id, voz); }
        voz.saiu = null;
        voz.g.gain.setTargetAtTime(v.ganho, t, sg);
        voz.p.pan.setTargetAtTime(v.pan, t, 0.1);
        for (const { o, m } of voz.oscs) o.frequency.setTargetAtTime(v.freq * m, t, sf);
      }
      for (const [id, voz] of this.vozes) {
        if (presentes.has(id)) continue;
        voz.g.gain.setTargetAtTime(0, t, sg);
        if (voz.saiu === null) voz.saiu = t;
        if (t - voz.saiu > 4) { // já em silêncio: libera os nós
          voz.oscs.forEach(({ o }) => o.stop());
          voz.g.disconnect(); voz.p.disconnect();
          this.vozes.delete(id);
        }
      }
    }

    // nota com envelope (ataque linear, decaimento exponencial -60 dB, rampa final sem clique)
    nota(ev) {
      if (this.pendentes > 150) return;
      const ctx = this.ctx, T = this.cfg.timbres[ev.timbre];
      const t0 = ctx.currentTime + 0.03 + (ev.atraso || 0);
      const atk = Math.max(T.ataque, 0.002);
      const saida = ctx.createGain(), pan = ctx.createStereoPanner();
      pan.pan.value = ev.pan || 0;
      saida.connect(pan); pan.connect(this.barra);
      let fim = 0;
      T.parciais.forEach(([m, a], k) => {
        const f = ev.freq * m;
        if (f >= ctx.sampleRate * 0.45) return;
        const dec = T.decai / (1 + 0.5 * k), pico = a * ev.vel * this.cfg.ganho_evento;
        const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = f;
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, t0);
        g.gain.linearRampToValueAtTime(pico, t0 + atk);
        g.gain.exponentialRampToValueAtTime(pico * 0.001, t0 + atk + dec);
        g.gain.linearRampToValueAtTime(0, t0 + atk + dec + 0.01);
        o.connect(g); g.connect(saida);
        o.start(t0); o.stop(t0 + atk + dec + 0.02);
        fim = Math.max(fim, atk + dec + 0.02);
      });
      this.limpar(saida, pan, (ev.atraso || 0) + fim + 0.5);
    }

    bumbo(ev) {
      if (this.pendentes > 150) return;
      const ctx = this.ctx, t0 = ctx.currentTime + 0.03 + (ev.atraso || 0);
      const pico = ev.vel * this.cfg.ganho_evento * 1.6;
      const o = ctx.createOscillator(); o.type = 'sine';
      o.frequency.setValueAtTime(130, t0);
      o.frequency.exponentialRampToValueAtTime(45, t0 + 0.25);
      const g = ctx.createGain();
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(pico, t0 + 0.004);
      g.gain.exponentialRampToValueAtTime(pico * 0.001, t0 + 0.454);
      g.gain.linearRampToValueAtTime(0, t0 + 0.464);
      o.connect(g); g.connect(this.barra);
      o.start(t0); o.stop(t0 + 0.48);
      this.limpar(g, null, (ev.atraso || 0) + 1);
    }

    limpar(a, b, seg) {
      this.pendentes++;
      setTimeout(() => { try { a.disconnect(); if (b) b.disconnect(); } catch (e) { /* já solto */ } this.pendentes--; }, seg * 1000 + 100);
    }

    aplicar(som) {
      this.atualizarVozes(som.vozes);
      for (const ev of som.eventos) (ev.tipo === 'bumbo' ? this.bumbo(ev) : this.nota(ev));
    }
  }

  // --------------------------------------------------------------- gravação
  async function iniciarGravacao() {
    const s = estado.sintese;
    if (!s || estado.gravacao) return;
    if (!s.ctx.audioWorklet) { alert('Este navegador não suporta AudioWorklet; a gravação não está disponível.'); return; }
    if (!s.gravadorPronto) { await s.ctx.audioWorklet.addModule('/gravador.js'); s.gravadorPronto = true; }
    const no = new AudioWorkletNode(s.ctx, 'gravador', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
    const mudo = s.ctx.createGain(); mudo.gain.value = 0;
    const rec = { no, mudo, esq: [], dir: [], amostras: 0, inicio: performance.now(), taxa: s.ctx.sampleRate };
    no.port.onmessage = (e) => { rec.esq.push(e.data[0]); rec.dir.push(e.data[1]); rec.amostras += e.data[0].length; };
    s.comp.connect(no); no.connect(mudo); mudo.connect(s.ctx.destination);
    estado.gravacao = rec;
    $('btn-gravar').textContent = '⏹ Parar e baixar .wav';
    $('gravando').classList.remove('oculto');
    rec.timer = setInterval(() => {
      const seg = Math.floor((performance.now() - rec.inicio) / 1000);
      $('gravando-tempo').textContent = Math.floor(seg / 60) + ':' + String(seg % 60).padStart(2, '0');
      if (seg > 15 * 60) pararGravacao(); // limite de segurança: 15 min
    }, 500);
  }

  function codificarWav(rec) {
    const n = rec.amostras, buf = new ArrayBuffer(44 + n * 4), v = new DataView(buf);
    const txt = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    txt(0, 'RIFF'); v.setUint32(4, 36 + n * 4, true); txt(8, 'WAVE'); txt(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 2, true);
    v.setUint32(24, rec.taxa, true); v.setUint32(28, rec.taxa * 4, true);
    v.setUint16(32, 4, true); v.setUint16(34, 16, true); txt(36, 'data'); v.setUint32(40, n * 4, true);
    let o = 44, pico = 0, soma = 0;
    for (let b = 0; b < rec.esq.length; b++) {
      const e = rec.esq[b], d = rec.dir[b];
      for (let i = 0; i < e.length; i++) {
        const x = Math.max(-1, Math.min(1, e[i])), y = Math.max(-1, Math.min(1, d[i]));
        v.setInt16(o, Math.round(x * 32767), true); v.setInt16(o + 2, Math.round(y * 32767), true);
        o += 4; pico = Math.max(pico, Math.abs(x), Math.abs(y)); soma += x * x + y * y;
      }
    }
    estado.ultimaGravacao = { duracao: n / rec.taxa, pico, rms: Math.sqrt(soma / Math.max(1, 2 * n)), bytes: buf.byteLength };
    return new Blob([buf], { type: 'audio/wav' });
  }

  function pararGravacao() {
    const rec = estado.gravacao;
    if (!rec) return;
    estado.gravacao = null;
    clearInterval(rec.timer);
    try { estado.sintese.comp.disconnect(rec.no); rec.no.disconnect(); rec.mudo.disconnect(); } catch (e) { /* ok */ }
    $('btn-gravar').textContent = '⏺ Gravar sessão (.wav)';
    $('gravando').classList.add('oculto');
    if (rec.amostras === 0) return;
    const blob = codificarWav(rec), url = URL.createObjectURL(blob);
    const d = new Date(), p = (x) => String(x).padStart(2, '0');
    const a = document.createElement('a');
    a.href = url;
    a.download = `netsound-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.wav`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // ------------------------------------------------------------- interface
  function montarClasses() {
    const box = $('classes');
    box.innerHTML = '';
    for (const nome of estado.cfg.ordem_classes) {
      const c = estado.cfg.classes[nome];
      const div = document.createElement('div');
      div.className = 'classe'; div.id = 'cl-' + nome;
      div.innerHTML = '<span class="nome"></span><div class="barra"><i></i></div><b>0</b>';
      div.querySelector('.nome').textContent = c.rotulo + ' · ' + c.timbre;
      div.querySelector('.nome').style.color = c.cor;
      div.querySelector('i').style.background = c.cor;
      box.appendChild(div);
    }
  }

  function registrar(texto) {
    estado.log.unshift('<b>' + hora() + '</b> ' + texto);
    estado.log = estado.log.slice(0, 8);
    $('log').innerHTML = estado.log.map((l) => '<li>' + l + '</li>').join('');
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function atualizarPainel(q) {
    $('m-in').textContent = fmtTaxa(q.entrada_bps);
    $('m-out').textContent = fmtTaxa(q.saida_bps);
    $('m-in-pps').textContent = Math.round(q.entrada_pps) + ' pct/s';
    $('m-out-pps').textContent = Math.round(q.saida_pps) + ' pct/s';
    $('m-con').textContent = q.conexoes.total;
    $('m-rem').textContent = q.conexoes.remotos + ' destinos';
    $('m-viz').textContent = q.vizinhos.length;
    $('n-in').textContent = q.som.info.nota_entrada;
    $('n-out').textContent = q.som.info.nota_saida;
    const max = Math.max(8, ...Object.values(q.conexoes.por_classe));
    for (const nome of estado.cfg.ordem_classes) {
      const n = q.conexoes.por_classe[nome] || 0, el = $('cl-' + nome);
      el.querySelector('i').style.width = Math.min(100, (n / max) * 100) + '%';
      el.querySelector('b').textContent = n;
    }
    const novosViz = new Set(q.novos_vizinhos);
    const chips = $('dispositivos');
    chips.innerHTML = q.vizinhos.length
      ? q.vizinhos.map((d) => '<span class="chip' + (novosViz.has(d.id) ? ' novo' : '') + '" title="' + esc(d.mac) + '">' +
        esc(d.ip) + ' <small>' + esc(d.mac.slice(-8)) + '</small></span>').join('')
      : '<span class="vazio">nenhum vizinho visto ainda</span>';
    for (const n of q.novas.slice(0, 3)) {
      registrar('nova conexão <b>' + esc(estado.cfg.classes[n.classe].rotulo) + '</b> → ' + esc(n.remoto) + ':' + n.porta + ' (' + n.proto + ')');
      const el = $('cl-' + n.classe);
      el.classList.add('ping'); setTimeout(() => el.classList.remove('ping'), 250);
    }
    if (q.novas.length > 3) registrar('+' + (q.novas.length - 3) + ' novas conexões');
    for (const m of q.novos_vizinhos) registrar('novo dispositivo na LAN: <b>' + esc(m) + '</b>');
    if (q.som.info.pico) registrar('<b>pico de tráfego!</b> acorde intensidade ' + q.som.info.pico);
  }

  const escala = (b) => Math.max(0, Math.min(1, Math.log10(Math.max(b, 1) / 100) / Math.log10(1e6)));

  function desenhar() {
    const cv = $('grafico'), g = cv.getContext('2d'), W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H);
    g.strokeStyle = '#232b47'; g.fillStyle = '#6b7599'; g.font = '11px sans-serif'; g.lineWidth = 1;
    [['100 B/s', 0], ['10 KB/s', 2 / 6], ['1 MB/s', 4 / 6], ['100 MB/s', 1]].forEach(([r, f]) => {
      const y = H - 18 - f * (H - 30);
      g.beginPath(); g.moveTo(60, y); g.lineTo(W, y); g.stroke(); g.fillText(r, 4, y + 4);
    });
    const N = estado.hist.length, passo = (W - 60) / 120;
    const linha = (campo, cor) => {
      g.strokeStyle = cor; g.lineWidth = 2; g.beginPath();
      estado.hist.forEach((h, i) => {
        const x = W - (N - 1 - i) * passo, y = H - 18 - escala(h[campo]) * (H - 30);
        if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
      });
      g.stroke();
    };
    linha('in', '#4cc9f0'); linha('out', '#ff9f1c');
    estado.hist.forEach((h, i) => {
      if (h.pico) { const x = W - (N - 1 - i) * passo; g.fillStyle = '#ffd166'; g.fillText('★', x - 5, 14); }
    });
    // espectro
    const ce = $('espectro'), ge = ce.getContext('2d');
    ge.clearRect(0, 0, ce.width, ce.height);
    if (estado.sintese) {
      const dados = new Uint8Array(estado.sintese.analisador.frequencyBinCount);
      estado.sintese.analisador.getByteFrequencyData(dados);
      const barras = 48, bw = ce.width / barras;
      for (let i = 0; i < barras; i++) {
        const k = Math.floor(Math.pow(i / barras, 2) * dados.length * 0.6) + 1;
        const h = (dados[k] / 255) * ce.height;
        ge.fillStyle = 'hsl(' + (200 - i * 2) + ',70%,60%)';
        ge.fillRect(i * bw + 1, ce.height - h, bw - 2, h);
      }
    }
    requestAnimationFrame(desenhar);
  }

  function aoReceber(q) {
    estado.ultimaMsg = Date.now();
    if (q.erro) { registrar('erro de leitura: ' + esc(q.erro)); return; }
    estado.quadros++;
    estado.ultimo = q;
    estado.hist.push({ in: q.entrada_bps, out: q.saida_bps, pico: q.som.info.pico });
    const maxh = Math.ceil(60 / estado.cfg.intervalo);
    if (estado.hist.length > maxh) estado.hist.shift();
    atualizarPainel(q);
    if (estado.sintese && estado.sintese.ctx.state === 'running') estado.sintese.aplicar(q.som);
  }

  function conectar() {
    const selo = $('selo-conexao');
    const marcar = (ok) => { selo.textContent = ok ? 'conectado' : 'reconectando…'; selo.className = 'selo ' + (ok ? 'ok' : 'erro'); };
    if (!window.EventSource) { setInterval(sondar, estado.cfg.intervalo * 1000); return; }
    const es = new EventSource('/api/eventos');
    es.onopen = () => marcar(true);
    es.onerror = () => marcar(false);
    es.onmessage = (e) => { marcar(true); try { aoReceber(JSON.parse(e.data)); } catch (err) { estado.erros.push('quadro: ' + err); } };
    // plano B: se o fluxo travar, consulta /api/estado
    setInterval(() => { if (Date.now() - estado.ultimaMsg > 5000) sondar(); }, 2500);
  }
  async function sondar() {
    try { const r = await fetch('/api/estado'); aoReceber(await r.json()); } catch (e) { /* tenta de novo */ }
  }

  async function alternarAudio() {
    const btn = $('btn-iniciar');
    if (!estado.sintese) {
      estado.sintese = new Sintese(estado.cfg);
      estado.sintese.volume(parseFloat($('volume').value));
      estado.sintese.quantidadeEfeitos(parseFloat($('reverb').value));
      await estado.sintese.ctx.resume();
      btn.textContent = '■ Parar áudio'; btn.classList.add('ativo');
      $('btn-gravar').disabled = false; $('btn-gravar').title = '';
      $('aviso-audio').classList.add('oculto');
      if (estado.ultimo) estado.sintese.aplicar(estado.ultimo.som);
    } else if (estado.sintese.ctx.state === 'running') {
      if (estado.gravacao) pararGravacao();
      await estado.sintese.ctx.suspend();
      btn.textContent = '▶ Retomar áudio'; btn.classList.remove('ativo');
    } else {
      await estado.sintese.ctx.resume();
      btn.textContent = '■ Parar áudio'; btn.classList.add('ativo');
    }
  }

  async function iniciar() {
    try {
      estado.cfg = await (await fetch('/api/config')).json();
    } catch (e) {
      $('selo-conexao').textContent = 'servidor indisponível'; $('selo-conexao').className = 'selo erro'; return;
    }
    const sm = $('selo-modo');
    sm.textContent = estado.cfg.demo ? 'DEMO · tráfego simulado' : 'rede real';
    sm.className = 'selo ' + (estado.cfg.demo ? 'demo' : 'ok');
    montarClasses();
    $('btn-iniciar').addEventListener('click', alternarAudio);
    $('btn-gravar').addEventListener('click', () => (estado.gravacao ? pararGravacao() : iniciarGravacao()));
    $('volume').addEventListener('input', (e) => {
      $('volume-txt').textContent = Math.round(e.target.value * 100) + '%';
      if (estado.sintese) estado.sintese.volume(parseFloat(e.target.value));
    });
    $('reverb').addEventListener('input', (e) => { if (estado.sintese) estado.sintese.quantidadeEfeitos(parseFloat(e.target.value)); });
    conectar();
    requestAnimationFrame(desenhar);
  }
  iniciar();
})();
