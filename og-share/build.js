// Draws the zkas-share preview image with today's share of Kaspa hashrate, and gives it a fresh
// ?v= so X fetches the new picture instead of its cached one. Same sources and rule as the page.
const fs = require("fs"), path = require("path"), puppeteer = require("puppeteer-core");
const SITE = path.join(__dirname, "../share-site/public");

const agreedHigh = vals => { const v = vals.filter(x => x > 0).sort((a, b) => b - a); if (v.length < 2) return v[0] || 0;
  for (let i = 0; i < v.length; i++) if (v.some((w, j) => j !== i && Math.abs(v[i] - w) <= 0.03 * v[i])) return v[i]; return v[Math.floor(v.length / 2)]; };
const avg = a => a && a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;
const get = u => fetch(u, {signal: AbortSignal.timeout(20000)}).then(r => r.ok ? r.json() : null).catch(() => null);

(async () => {
  const [dag, pulse, pool, kh, kdag] = await Promise.all([
    get("https://explorer.zkas.info/api/info/blockdag"), get("https://explorer.zkas.info/api/info/pulse"),
    get("https://mining-pool.zkas.info/api/stats"), get("https://api.kaspa.org/info/hashrate?stringOnly=false"),
    get("https://api.kaspa.org/info/blockdag")]);
  const H = JSON.parse(fs.readFileSync(path.join(__dirname, "../data/history.json"), "utf8")), s = H.samples[H.samples.length - 1];
  const z = agreedHigh([dag && dag.difficulty * 2, pulse && avg(pulse.hashrateBins), pool && pool.networkHashrate])
    || agreedHigh([s.zkas.difficulty, s.zkas.pulse, s.zkas.pool]);
  const k = agreedHigh([kh && kh.hashrate * 1e12, kdag && kdag.difficulty * 20]) || s.kaspa.hashrate;
  const share = z / k * 100;
  if (!(share > 0 && share < 100)) throw new Error("share out of range: " + share);
  const now = share.toFixed(1);

  const html = path.join(__dirname, "og.html");
  fs.writeFileSync(html, fs.readFileSync(path.join(__dirname, "template.html"), "utf8").replace("{{NOW}}", now));
  const browser = await puppeteer.launch({executablePath: process.env.CHROME_PATH, args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.setViewport({width: 1200, height: 630});
  await page.goto("file://" + html, {waitUntil: "networkidle0"});
  await page.evaluate(async () => {
    await document.fonts.ready;
    // Wider numbers (e.g. 26.2%) shrink until they fit the space beside the logo.
    const b = document.querySelector(".big"), room = b.parentElement.clientWidth;
    b.style.display = "inline-block"; b.style.whiteSpace = "nowrap"; b.style.paddingRight = "8px";
    for (let px = 200; b.scrollWidth > room && px > 80; px -= 4) b.style.fontSize = px + "px";
  });
  await page.screenshot({path: path.join(SITE, "og.png")});
  await browser.close();
  fs.unlinkSync(html);

  const v = new Date().toISOString().slice(0, 10), idx = path.join(SITE, "index.html");
  fs.writeFileSync(idx, fs.readFileSync(idx, "utf8").replace(/og\.png\?v=[^"]*/g, "og.png?v=" + v));
  console.log(`share ${now}%  v=${v}`);
})().catch(e => { console.error(e); process.exit(1); });
