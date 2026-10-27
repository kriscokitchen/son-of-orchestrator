#!/usr/bin/env node
// Шрифты для направления (Фаза 3): есть ли у гарнитуры кириллица и шкала размеров.
//
//   node fonts.mjs check "Satoshi" --css "https://api.fontshare.com/v2/css?f[]=satoshi@400"
//   node fonts.mjs check "Manrope" --css "https://fonts.googleapis.com/css2?family=Manrope"
//   node fonts.mjs check "Своя" --file fonts/svoya.woff2
//   node fonts.mjs scale --min 16 --max 18 --ratio 1.2 --max-ratio 1.25 [--from 360 --to 1440] [--steps 5] [--down 2]
//
// check проверяет по отрисовке, а не по описанию шрифта: текст «ЖЩЮЯжщюя» шрифтом
// с запасным monospace и с запасным serif меряется против самих запасных. Ширины
// совпали в обоих случаях — букв в шрифте нет, браузер молча взял запасной. Так
// ловится и шрифт, у которого кириллицы нет вовсе (у Fontshare — ни у одного из
// 100 семейств), и подмножество Google Fonts без кириллического набора. Латиница
// меряется так же: не отличается — шрифт не загрузился, и вывод о кириллице
// делать не из чего.
//
// scale считает плавную шкалу по методу utopia.fyi: на ширине --from размер
// --min, на --to размер --max, шаги вверх умножаются на --ratio (на малом экране)
// и --max-ratio (на большом). Выход — CSS-переменные --step-N с clamp() в rem.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [cmd, ...rest] = process.argv.slice(2);
const opt = { css: [], file: null };
const pos = [];
for (let i = 0; i < rest.length; i++) {
  const a = rest[i];
  if (a === '--css') opt.css.push(rest[++i]);
  else if (a === '--file') opt.file = rest[++i];
  else if (a.startsWith('--')) opt[a.slice(2)] = Number(rest[++i]);
  else pos.push(a);
}

if (cmd === 'scale') {
  const from = opt.from ?? 360, to = opt.to ?? 1440;
  const min = opt.min, max = opt.max, r1 = opt.ratio, r2 = opt['max-ratio'] ?? opt.ratio;
  if (!min || !max || !r1) {
    console.error('Нужны --min, --max, --ratio: node fonts.mjs scale --min 16 --max 18 --ratio 1.2 --max-ratio 1.25');
    process.exit(2);
  }
  const rem = (px) => +(px / 16).toFixed(4);
  console.log(`/* шкала: ${min}→${max}px, ${r1}→${r2}, ширина ${from}→${to}px (метод utopia.fyi) */`);
  for (let s = -(opt.down ?? 2); s <= (opt.steps ?? 5); s++) {
    // Размеры округляются до сотых до расчёта наклона — как в utopia-core.
    const lo = +(min * r1 ** s).toFixed(2), hi = +(max * r2 ** s).toFixed(2);
    const slope = (hi - lo) / (to - from);
    const base = lo - slope * from;
    const [a, b] = lo <= hi ? [lo, hi] : [hi, lo];
    console.log(`--step-${s}: clamp(${rem(a)}rem, ${rem(base)}rem + ${+(slope * 100).toFixed(4)}vw, ${rem(b)}rem);`);
  }
  process.exit(0);
}

if (cmd !== 'check' || !pos[0] || (!opt.css.length && !opt.file)) {
  console.error('node fonts.mjs check "<гарнитура>" --css <ссылка> | --file <файл>\nnode fonts.mjs scale --min 16 --max 18 --ratio 1.2');
  process.exit(2);
}

let playwright;
try {
  playwright = createRequire(path.join(process.cwd(), 'package.json'))('playwright');
} catch {
  console.error('В проекте нет Playwright. Поставить: npm i -D playwright && npx playwright install chromium');
  process.exit(2);
}

const family = pos[0];
const face = opt.file
  ? `@font-face{font-family:"${family}";src:url("${pathToFileURL(path.resolve(opt.file)).href}")}`
  : '';
const html = `<!doctype html><meta charset="utf-8">
${opt.css.map((u) => `<link rel="stylesheet" href="${u.replace(/"/g, '&quot;')}">`).join('')}
<style>${face} span{font-size:48px;white-space:nowrap}</style><body></body>`;
const tmp = path.join(fs.mkdtempSync(path.join(process.env.TMPDIR || '/tmp', 'fonts-')), 'check.html');
fs.writeFileSync(tmp, html);

const browser = await playwright.chromium.launch();
const page = await browser.newPage();
try {
  await page.goto(pathToFileURL(tmp).href, { waitUntil: 'load', timeout: 20000 });
  const r = await page.evaluate(async (fam) => {
    await Promise.all([
      document.fonts.load(`48px "${fam}"`, 'Hamburg'),
      document.fonts.load(`48px "${fam}"`, 'ЖЩЮЯжщюя'),
    ]).catch(() => {});
    await document.fonts.ready;
    const width = (text, stack) => {
      const s = document.createElement('span');
      s.style.fontFamily = stack;
      s.textContent = text;
      document.body.append(s);
      const w = s.getBoundingClientRect().width;
      s.remove();
      return w;
    };
    const differs = (text) => ['monospace', 'serif'].some((fb) => width(text, `"${fam}", ${fb}`) !== width(text, fb));
    // Только буквы, без пробелов и знаков: пробел рисует сам шрифт, и ширина
    // отличалась бы даже без единой кириллической буквы.
    return { latin: differs('HamburgQuiz'), cyr: differs('ЖЩЮЯжщюяСъешь') };
  }, family);
  if (!r.latin) console.log(`${family}: шрифт не загрузился — проверь ссылку или файл; о кириллице вывода нет`);
  else console.log(`${family}: кириллица ${r.cyr ? 'есть' : 'НЕТ — русский текст уйдёт в запасной шрифт'}`);
  process.exitCode = r.latin && r.cyr ? 0 : 1;
} catch (e) {
  console.log(`${family}: не проверен — ${String(e.message || e).split('\n')[0]}`);
  process.exitCode = 2;
} finally {
  await browser.close().catch(() => {});
  fs.rmSync(path.dirname(tmp), { recursive: true, force: true });
}
