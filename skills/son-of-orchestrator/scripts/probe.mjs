#!/usr/bin/env node
// Функциональная проверка страницы для приёмки тикетов: текст на месте, кнопка
// нажимается, поле заполняется, в консоли нет ошибок. Снимков не делает — внешний
// вид проверяет только shots.mjs на Chromium.
//
//   node probe.mjs <адрес-или-файл> [шаг ...] [--engine auto|chromium|obscura]
//                  [--width 1440] [--timeout мс]
//
// Шаги идут по порядку на одной странице:
//   text:Копилка        текст виден на странице
//   no:Ошибка           такого текста на странице нет
//   click:Добавить      нажать на элемент с этим текстом
//   fill:#amount=1234   заполнить поле по CSS-селектору
//   eval:JS             выражение на странице должно вернуть истину
//
// Движок. chromium — Playwright проекта, как в shots.mjs. obscura — лёгкий браузер
// на своём движке (github.com/h4ckf0r0day/obscura): не нужен скачанный Chromium,
// поднимается за доли секунды. auto — chromium, а не запустился — obscura.
//
// У obscura своя раскладка: видимость и перекрытие элементов он считает не так, как
// Chrome, и мягче его в ограничениях — запускает модули у страницы из file://,
// которые Chrome блокирует. Поэтому каждый pass на obscura помечен «[obscura]»:
// для пунктов о видимости, нажатиях и ограничениях браузера такой pass — не
// доказательство, их подтверждает chromium.
//
// obscura берётся только из OBSCURA_BIN, не из PATH, и только если сумма бинарника
// совпала с проверенной навыком (иная версия — OBSCURA_TRUST=1). Открывает только
// свои страницы — file:// и localhost: ему снимается защита от доступа к файлам и
// внутренней сети, и чужой странице её отдавать нельзя.
//
// Код выхода 1 — какой-то шаг не прошёл или страница не загрузилась. Ошибки консоли
// печатаются, но сами по себе код не меняют: «без ошибок в консоли» — отдельный
// пункт приёмки, если он нужен.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// Сборки obscura, которые навык проверял: sha256 бинарников, не архива.
const TRUSTED = {
  '8f6f3a7010b51b5c130bd55388a044e122bd04e109faf34f11c9e22a5d3a5b39': 'obscura 0.2.5 macOS arm64',
  '0d2fa2bab985c7bc3430d70307fdf0ab095605029953fbee1f5d5f9fb20a895e': 'obscura-worker 0.2.5 macOS arm64',
};

const args = process.argv.slice(2);
const opt = { engine: 'auto', timeout: 15000, width: 1440 };
const rest = [];
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '--engine') opt.engine = args[++i];
  else if (a === '--timeout') opt.timeout = Number(args[++i]);
  else if (a === '--width') opt.width = Number(args[++i]);
  else rest.push(a);
}
if (!rest.length || !['auto', 'obscura', 'chromium'].includes(opt.engine)) {
  console.error('Нужен адрес или файл: node probe.mjs dist/index.html text:Копилка click:Добавить');
  process.exit(2);
}
const [target, ...steps] = rest;
const url = fs.existsSync(target) ? pathToFileURL(path.resolve(target)).href : target;
const viewport = { width: opt.width, height: opt.width < 600 ? 844 : 900 }; // как в shots.mjs

let playwright;
try {
  playwright = createRequire(path.join(process.cwd(), 'package.json'))('playwright');
} catch {
  console.error('В проекте нет Playwright. Поставить: npm i -D playwright');
  process.exit(2);
}

// Обещание с пределом: зависшая страница не отвечает, и ждать её нельзя.
const limit = (work, ms, what) => Promise.race([
  work,
  new Promise((_, no) => setTimeout(() => no(new Error(what)), ms).unref()),
]);

// Своя страница: файл или localhost. Только им obscura открывает файлы и сеть.
function ownPage(u) {
  try {
    const { protocol, hostname } = new URL(u);
    if (protocol === 'file:') return { file: true };
    if ((protocol === 'http:' || protocol === 'https:') && ['localhost', '127.0.0.1', '[::1]'].includes(hostname)) return { local: true };
  } catch {}
  return null;
}

const sha256 = (f) => createHash('sha256').update(fs.readFileSync(f)).digest('hex');

// Почему obscura нельзя — строкой; можно — null.
function obscuraRefusal() {
  const bin = process.env.OBSCURA_BIN;
  if (!bin) return 'OBSCURA_BIN не задан';
  if (!fs.existsSync(bin)) return `OBSCURA_BIN указывает на несуществующий файл: ${bin}`;
  if (!ownPage(url)) return 'адрес не file:// и не localhost — obscura открывает только свои страницы';
  if (process.env.OBSCURA_TRUST !== '1') {
    const worker = path.join(path.dirname(bin), 'obscura-worker');
    for (const f of [bin, ...(fs.existsSync(worker) ? [worker] : [])]) {
      if (!TRUSTED[sha256(f)]) return `сумма ${path.basename(f)} не совпала с проверенной навыком (иная версия — OBSCURA_TRUST=1)`;
    }
  }
  return null;
}

const freePort = () => new Promise((ok, no) => {
  const s = net.createServer().once('error', no);
  s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => ok(port)); });
});

// Obscura — CDP-сервер на свободном порту только для этого запуска и только на
// 127.0.0.1. Доступ к файлам или к localhost открывается тот, что нужен странице.
let child = null;
const killChild = () => { if (child && child.exitCode === null) child.kill('SIGKILL'); };
process.on('exit', killChild);
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { killChild(); process.exit(130); });

async function startObscura() {
  const port = await freePort();
  const own = ownPage(url);
  child = spawn(process.env.OBSCURA_BIN, ['serve', '--host', '127.0.0.1', '--port', String(port), '--quiet',
    ...(own.file ? ['--allow-file-access'] : []), ...(own.local ? ['--allow-private-network'] : [])],
    { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', (d) => { stderr += d; });
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`obscura вышел сразу: ${stderr.trim().split('\n')[0] || child.exitCode}`);
    try {
      return await playwright.chromium.connectOverCDP({ endpointURL: `ws://127.0.0.1:${port}`, timeout: 1000 });
    } catch {
      await new Promise((r) => setTimeout(r, 150));
    }
  }
  killChild();
  throw new Error('obscura не ответил за 5 с');
}

async function viaObscura() {
  const browser = await startObscura();
  const page = await (await browser.newContext({ viewport })).newPage();
  return { engine: 'obscura', page, close: async () => { await limit(browser.close(), 5000, 'close').catch(() => {}); killChild(); } };
}

async function viaChromium() {
  const browser = await playwright.chromium.launch();
  const page = await browser.newPage({ viewport });
  // Зависший рендерер может не дать закрыться — тогда процесс убивается, а не остаётся.
  return { engine: 'chromium', page, close: () => limit(browser.close(), 10000, 'close').catch(() => browser.process()?.kill('SIGKILL')) };
}

async function open() {
  if (opt.engine === 'obscura') {
    const no = obscuraRefusal();
    if (no) { console.error(`obscura нельзя: ${no}.`); process.exit(2); }
    return viaObscura();
  }
  try {
    return await viaChromium();
  } catch (e) {
    const why = String(e.message || e).split('\n')[0];
    if (opt.engine === 'chromium') { console.error(`chromium не запустился: ${why}`); process.exit(2); }
    const no = obscuraRefusal();
    if (no) { console.error(`chromium не запустился (${why}); obscura нельзя: ${no}.`); process.exit(2); }
    console.log(`chromium не запустился (${why}) — проверяю на obscura`);
    return viaObscura();
  }
}

const t0 = Date.now();
const { engine, page, close } = await open();
page.setDefaultTimeout(opt.timeout);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e.message || e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

let failed = 0;
let passed = 0;
const tag = engine === 'obscura' ? '  [obscura]' : '';
const say = (ok, text) => { if (ok) passed++; else failed++; console.log(`${ok ? 'pass' : 'FAIL'}${ok ? tag : ''}  ${text}`); };

try {
  await limit(page.goto(url, { waitUntil: 'load' }), opt.timeout, `страница не загрузилась за ${opt.timeout / 1000} с`);
  console.log(`движок: ${engine}  ширина: ${viewport.width}  загрузка: ${Date.now() - t0} мс  ${url}`);
} catch (e) {
  console.log(`движок: ${engine}`);
  say(false, `загрузка — ${String(e.message || e).split('\n')[0]}`);
  for (const x of errors) console.log(`    ${x}`);
  await close();
  process.exit(1);
}

let hung = false;
for (const step of steps) {
  if (hung) { say(false, `${step} — не проверен: страница зависла на прошлом шаге`); continue; }
  const [kind, ...tail] = step.split(':');
  const arg = tail.join(':');
  try {
    await limit((async () => {
      if (kind === 'text') {
        await page.getByText(arg, { exact: false }).first().waitFor({ state: 'visible' });
        say(true, step);
      } else if (kind === 'no') {
        say(await page.getByText(arg, { exact: false }).count() === 0, step);
      } else if (kind === 'click') {
        await page.getByText(arg, { exact: false }).first().click();
        say(true, step);
      } else if (kind === 'fill') {
        const [sel, ...val] = arg.split('=');
        await page.locator(sel).first().fill(val.join('='));
        say(true, step);
      } else if (kind === 'eval') {
        say(Boolean(await page.evaluate(arg)), step);
      } else {
        say(false, `${step} — неизвестный шаг (text: no: click: fill: eval:)`);
      }
    })(), opt.timeout, `не уложился в ${opt.timeout / 1000} с — скрипт страницы завис?`);
  } catch (e) {
    say(false, `${step} — ${String(e.message || e).split('\n')[0]}`);
    hung = String(e.message || e).includes('скрипт страницы завис');
  }
}

if (errors.length) {
  console.log(`ошибок в консоли: ${errors.length}`);
  for (const x of errors) console.log(`    ${x}`);
}
if (engine === 'obscura' && passed) {
  console.log('[obscura] pass о видимости, нажатиях и ограничениях браузера подтверди на chromium');
}
await close();
process.exit(failed ? 1 : 0);
