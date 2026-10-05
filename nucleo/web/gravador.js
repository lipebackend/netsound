// AudioWorklet que apenas copia os blocos de áudio para a página (gravação do .wav).
class Gravador extends AudioWorkletProcessor {
  process(entradas) {
    const e = entradas[0];
    if (e && e.length) {
      this.port.postMessage([e[0].slice(), (e[1] || e[0]).slice()]);
    }
    return true;
  }
}
registerProcessor('gravador', Gravador);
