#!/usr/bin/env node
// Снимки экранов для приёмки тикетов — на Playwright, который стоит в проекте.
//
//   node shots.mjs <адрес-или-файл> [экран ...] [--out ПАПКА] [--width 1440] [--mobile] [--wait мс]
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
// Скрипт лежит в навыке, а Playwright — в проекте, поэтому он ищется от текущей
// папки, а не от папки скрипта.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const opt = { out: '.orchestrator/shots', width: 1440, mobile: false, wait: 500 };
const rest = [];
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '--out') opt.out = args[++i];
  else if (a === '--width') opt.width = Number(args[++i]);
  else if (a === '--wait') opt.wait = Number(args[++i]);
  else if (a === '--mobile') opt.mobile = true;
  else rest.push(a);
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

for (const width of widths) {
  const page = await browser.newPage({
    viewport: { width, height: width < 600 ? 844 : 900 },
    deviceScaleFactor: 2,
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e.message || e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

  for (const { name, how } of list) {
    errors.length = 0;
    try {
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
    } catch (e) {
      failed++;
      console.log(`${name}-${width}: не снят — ${String(e.message || e).split('\n')[0]}`);
    }
  }
  await page.close();
}
await browser.close();
process.exit(failed ? 1 : 0);
