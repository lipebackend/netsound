// Teste da página com Chrome headless (puppeteer-core).
// Uso: NODE_PATH=/tmp/pp/node_modules node testes/pagina.js [url] [caminho-do-chrome]
const puppeteer = require('puppeteer-core');
const url = process.argv[2] || 'http://127.0.0.1:8765/';
const chrome = process.argv[3] || '/usr/bin/google-chrome';
const espera = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({
    executablePath: chrome, headless: 'new',
    args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream'],
  });
  const page = await browser.newPage();
  const erros = [], avisos = [];
  page.on('console', (m) => { if (m.type() === 'error') erros.push(m.text()); else if (m.type() === 'warning') avisos.push(m.text()); });
  page.on('pageerror', (e) => erros.push('pageerror: ' + e.message));
  page.on('response', (r) => { if (r.status() >= 400) erros.push('HTTP ' + r.status() + ' ' + r.url()); });
  page.on('requestfailed', (r) => erros.push('requestfailed: ' + r.url()));
  await page.setViewport({ width: 1100, height: 1100 });
  await page.goto(url, { waitUntil: 'load' });
  await espera(2500);
  const antes = await page.evaluate(() => ({ quadros: window.__netsound.quadros, audio: !!window.__netsound.sintese }));
  await page.click('#btn-iniciar');
  await espera(6000);
  await page.click('#btn-gravar');           // inicia gravação do .wav no navegador
  await espera(8000);
  await page.screenshot({ path: process.env.CAPTURA || '/tmp/netsound-pagina.png' });
  await page.click('#btn-gravar');           // para (dispara o download)
  await espera(1000);
  const r = await page.evaluate(() => {
    const e = window.__netsound, s = e.sintese;
    return {
      quadros: e.quadros, estadoAudio: s && s.ctx.state, taxa: s && s.ctx.sampleRate, vozes: s && s.vozes.size,
      selo: document.getElementById('selo-conexao').textContent, modo: document.getElementById('selo-modo').textContent,
      classes: document.querySelectorAll('.classe').length, errosJS: e.erros, gravacao: e.ultimaGravacao,
      log: document.getElementById('log').innerText.split('\n').slice(0, 3),
    };
  });
  console.log(JSON.stringify({ antes, ...r, erros_console: erros, avisos_console: avisos }, null, 2));
  await browser.close();
  const ok = r.quadros > 5 && r.estadoAudio === 'running' && !erros.length && !r.errosJS.length &&
    r.gravacao && r.gravacao.duracao > 5 && r.gravacao.pico > 0.01;
  console.log(ok ? 'RESULTADO: OK' : 'RESULTADO: FALHA');
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('FALHA', e); process.exit(2); });
