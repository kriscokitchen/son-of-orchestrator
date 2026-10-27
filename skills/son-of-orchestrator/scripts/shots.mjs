#!/usr/bin/env node
// Снимки экранов для приёмки тикетов — на Playwright, который стоит в проекте.
//
//   node shots.mjs <адрес-или-файл> [экран ...] [--out ПАПКА] [--width 1440] [--mobile] [--wait мс]
//                  [--timeout мс] [--compare ЭТАЛОН.png]
//
// Экран:
//   plan               адрес + "#plan"
//   plan=#/plan        адрес + суффикс: "#…", "/…" или "?…"
//   plan=click:ПЛАН    открыть адрес и нажать на элемент с этим текстом
//   (без экранов)      один снимок по адресу, имя "page"
//
// Снимки ложатся в .orchestrator/shots/<экран>-<ширина>.png. --mobile добавляет
// снимок на 390 px. Ошибки консоли печатаются: пункт «без ошибок в консоли»
// проверяется этим же запуском.
//
// У каждого экрана предел времени (--timeout, по умолчанию 20 с). Страница, чей
// скрипт завис, не отвечает и на evaluate, а у него в Playwright таймаута нет:
// без предела окно тикета ждало бы вечно. Не уложился — экран «не снят», страница
// бросается, следующий экран снимается в новой.
//
// --compare кладёт рядом с каждым снимком основной ширины склейку «эталон | снимок»
// (<экран>-<ширина>-vs-comp.png): первый экран снимка и утверждённый эталон на одной
// ширине, бок о бок. Сверять по ней, а не по памяти об эталоне: по памяти любой
// снимок «похож».
//
// Попутно печатаются две вещи, которые на снимке не видны. Тяжёлые картинки —
// больше 300 КБ: главный тормоз первого экрана. Текст с контрастом ниже нормы WCAG
// (4.5:1, крупный — 3:1) — предупреждением, а не провалом: тихий второстепенный
// текст бывает решением направления. Роли, которые shape.md объявил тихими, не
// «чинятся»; остальное — да. Текст на картинке или градиенте не меряется.
//
// Скрипт лежит в навыке, а Playwright — в проекте, поэтому он ищется от текущей
// папки, а не от папки скрипта.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const opt = { out: '.orchestrator/shots', width: 1440, mobile: false, wait: 500, timeout: 20000 };
const rest = [];
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '--out') opt.out = args[++i];
  else if (a === '--width') opt.width = Number(args[++i]);
  else if (a === '--wait') opt.wait = Number(args[++i]);
  else if (a === '--timeout') opt.timeout = Number(args[++i]);
  else if (a === '--mobile') opt.mobile = true;
  else if (a === '--compare') opt.compare = args[++i];
  else rest.push(a);
}
if (opt.compare && !fs.existsSync(opt.compare)) {
  console.error(`Эталона нет: ${opt.compare}`);
  process.exit(2);
}
if (!rest.length) {
  console.error('Нужен адрес или файл: node shots.mjs dist/index.html plan goal --mobile');
  process.exit(2);
}

let playwright;
try {
  playwright = createRequire(path.join(process.cwd(), 'package.json'))('playwright');
} catch {
  console.error('В проекте нет Playwright. Поставить: npm i -D playwright && npx playwright install chromium');
  process.exit(2);
}

const [target, ...screens] = rest;
const base = fs.existsSync(target) ? pathToFileURL(path.resolve(target)).href : target;
const list = (screens.length ? screens : ['page=']).map((s) => {
  const [name, how = '#' + s] = s.includes('=') ? s.split(/=(.*)/s) : [s];
  return { name, how };
});

fs.mkdirSync(opt.out, { recursive: true });
const widths = opt.mobile ? [opt.width, 390] : [opt.width];
const browser = await playwright.chromium.launch();
let failed = 0;

// Обещание с пределом: зависшая страница не отвечает, и ждать её нельзя.
const limit = (work, ms, what) => Promise.race([
  work,
  new Promise((_, no) => setTimeout(() => no(new Error(what)), ms).unref()),
]);

// Контраст видимого текста по вычисленным стилям: цвет текста против первого
// непрозрачного фона вверх по дереву. Фон-картинка или градиент — не меряем.
function lowContrast() {
  const rgb = (c) => (c.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
  const hex = (c) => '#' + rgb(c).slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  const found = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.textContent.trim();
    const el = n.parentElement;
    if (!text || !el) continue;
    const st = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (st.visibility === 'hidden' || +st.opacity === 0 || !box.width || !box.height) continue;
    let bg = null;
    for (let a = el; a; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.backgroundImage !== 'none') break;
      const c = rgb(s.backgroundColor);
      if (c.length && (c[3] ?? 1) > 0.9) { bg = s.backgroundColor; break; }
    }
    if (!bg && getComputedStyle(document.documentElement).backgroundImage === 'none') bg = 'rgb(255, 255, 255)';
    if (!bg) continue;
    const [l1, l2] = [lum(rgb(st.color)), lum(rgb(bg))].sort((a, b) => b - a);
    const ratio = (l1 + 0.05) / (l2 + 0.05);
    const size = parseFloat(st.fontSize), bold = +st.fontWeight >= 700;
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    const key = hex(st.color) + hex(bg);
    if (ratio < need && !found.has(key)) found.set(key, `«${text.slice(0, 40)}» ${ratio.toFixed(2)}:1 (норма ${need}) ${hex(st.color)} на ${hex(bg)}`);
  }
  return [...found.values()].slice(0, 6);
}

// Склейка «эталон | снимок» на одной ширине — смотреть бок о бок.
async function sideBySide(shot, file) {
  const img = (b) => `data:image/png;base64,${b.toString('base64')}`;
  const p = await browser.newPage({ viewport: { width: 1600, height: 600 } });
  await p.setContent(`<body style="margin:0;background:#888;font:14px sans-serif;color:#fff">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px">
      <div>эталон<br><img style="width:100%" src="${img(fs.readFileSync(opt.compare))}"></div>
      <div>снимок<br><img style="width:100%" src="${img(shot)}"></div></div></body>`);
  await p.evaluate(() => Promise.all([...document.images].map((i) => i.decode())));
  await p.screenshot({ path: file, fullPage: true });
  await p.close();
}

for (const width of widths) {
  let page = null;
  const errors = [];
  const heavy = new Map();
  for (const { name, how } of list) {
    errors.length = 0;
    heavy.clear();
    if (!page) {
      page = await browser.newPage({
        viewport: { width, height: width < 600 ? 844 : 900 },
        deviceScaleFactor: 2,
      });
      page.setDefaultTimeout(opt.timeout);
      page.on('pageerror', (e) => errors.push(String(e.message || e)));
      page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
      page.on('response', async (r) => {
        if (r.request().resourceType() !== 'image') return;
        const size = Number(r.headers()['content-length']) || (await r.body().catch(() => Buffer.alloc(0))).length;
        if (size > 300 * 1024) heavy.set(r.url().split('/').pop().split('?')[0] || r.url(), size);
      });
    }
    try {
      await limit((async () => {
        if (how.startsWith('click:')) {
          await page.goto(base, { waitUntil: 'networkidle' });
          await page.getByText(how.slice(6), { exact: false }).first().click();
        } else {
          await page.goto(base + how, { waitUntil: 'networkidle' });
        }
        await page.evaluate(() => document.fonts && document.fonts.ready);
        await page.waitForTimeout(opt.wait); // входные анимации
        const file = path.join(opt.out, `${name}-${width}.png`);
        await page.screenshot({ path: file, fullPage: true });
        const over = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        console.log(`${file}${over ? '  ГОРИЗОНТАЛЬНЫЙ СКРОЛЛ' : ''}${errors.length ? `  ошибок в консоли: ${errors.length}` : ''}`);
        for (const e of errors) console.log(`    ${e}`);
        for (const [img, size] of heavy) console.log(`    ТЯЖЁЛАЯ КАРТИНКА ${img}: ${(size / 1024 / 1024).toFixed(1)} МБ`);
        for (const c of await page.evaluate(lowContrast)) console.log(`    контраст ниже нормы: ${c}`);
        if (opt.compare && width === opt.width) {
          const pair = path.join(opt.out, `${name}-${width}-vs-comp.png`);
          await sideBySide(await page.screenshot(), pair);
          console.log(`    рядом с эталоном: ${pair}`);
        }
      })(), opt.timeout, `страница не ответила за ${opt.timeout / 1000} с — скрипт страницы завис или она не загрузилась`);
    } catch (e) {
      failed++;
      console.log(`${name}-${width}: не снят — ${String(e.message || e).split('\n')[0]}`);
      for (const x of errors) console.log(`    ${x}`);
      // Зависшую страницу не ждём: закрываем без ожидания, следующий экран — в новой.
      page.close({ runBeforeUnload: false }).catch(() => {});
      page = null;
    }
  }
  if (page) await limit(page.close(), 5000, 'close').catch(() => {});
}
// Зависший рендерер может не дать закрыться — тогда процесс убивается, а не остаётся.
await limit(browser.close(), 10000, 'close').catch(() => browser.process()?.kill('SIGKILL'));
process.exit(failed ? 1 : 0);
