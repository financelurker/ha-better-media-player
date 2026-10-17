// Take README screenshots from the local demo instance.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8123';
const OUT = process.argv[2] || 'out';
const ONLY = process.argv[3] ? process.argv[3].split(',') : null;
const session = JSON.parse(fs.readFileSync('session.json', 'utf8'));
fs.mkdirSync(OUT, { recursive: true });

const want = (name) => !ONLY || ONLY.includes(name);

async function login(page) {
  await page.goto(BASE + '/');
  await page.waitForSelector('input[name="username"], ha-auth-flow', { timeout: 30000 });
  await page.locator('input[name="username"]').fill('demo');
  await page.locator('input[name="password"]').fill('demo-pass-2026');
  await page.keyboard.press('Enter');
  await page.waitForURL(/lovelace|home|overview/, { timeout: 30000 });
}

async function settle(page, ms = 2500) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
}

async function moreInfo(page, entityId) {
  await page.evaluate((id) => {
    const ha = document.querySelector('home-assistant');
    ha.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId: id }, bubbles: true, composed: true }));
  }, entityId);
  await settle(page, 2000);
}

async function shotDialog(page, file) {
  // crop to the dialog surface if we can find it, else full viewport
  const candidates = ['ha-dialog .mdc-dialog__surface', 'ha-md-dialog', 'wa-dialog [part="dialog"]', 'ha-adaptive-dialog', 'ha-dialog'];
  for (const sel of candidates) {
    const loc = page.locator(sel).first();
    if (await loc.count()) {
      const box = await loc.boundingBox();
      if (box && box.width > 100 && box.height > 100) {
        const pad = 0;
        const vp = page.viewportSize();
        const clip = {
          x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad),
          width: Math.min(vp.width - Math.max(0, box.x - pad), box.width + 2 * pad),
          height: Math.min(vp.height - Math.max(0, box.y - pad), box.height + 2 * pad),
        };
        await page.screenshot({ path: `${OUT}/${file}`, clip });
        console.log('dialog', file, sel, JSON.stringify(clip));
        return;
      }
    }
  }
  await page.screenshot({ path: `${OUT}/${file}` });
  console.log('dialog (full)', file);
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-proxy-server'] });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 640 }, deviceScaleFactor: 2, colorScheme: 'light' });
  const page = await ctx.newPage();
  await login(page);

  if (want('dashboard')) {
    await page.goto(BASE + '/lovelace/family-room');
    await settle(page, 4000);
    await page.screenshot({ path: `${OUT}/dashboard.png` });
    console.log('dashboard');
  }

  if (want('more-info')) {
    await page.setViewportSize({ width: 1400, height: 1000 });
    await page.goto(BASE + '/lovelace/family-room');
    await settle(page);
    await moreInfo(page, 'media_player.family_room');
    await shotDialog(page, 'more-info.png');
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1400, height: 640 });
  }

  if (want('device')) {
    await page.setViewportSize({ width: 1400, height: 1240 });
    await page.goto(`${BASE}/config/devices/device/${session.device_id}`);
    await settle(page, 4000);
    await page.screenshot({ path: `${OUT}/device.png` });
    await page.setViewportSize({ width: 1400, height: 640 });
    console.log('device');
  }

  if (want('options')) {
    await page.setViewportSize({ width: 1400, height: 1000 });
    await page.goto(`${BASE}/config/integrations/integration/better_media_player`);
    await settle(page, 3000);
    await page.screenshot({ path: `${OUT}/_integration.png` });
    // the cog / "Configure" button of the entry
    const btn = page.getByRole('button', { name: /configure/i }).first();
    if (await btn.count()) {
      await btn.click({ force: true });
    } else {
      await page.locator('ha-icon-button[label*="Configure" i], ha-button:has-text("Configure")').first().click();
    }
    await settle(page, 2500);
    await shotDialog(page, 'options-menu.png');
    for (const [label, file] of [['Power devices', 'options-devices.png'], ['Media players', 'options-players.png'], ['Times', 'options-times.png']]) {
      await page.getByText(label, { exact: true }).first().click({ force: true });
      await settle(page, 2500);
      await shotDialog(page, file);
      await page.getByRole('button', { name: /^(submit|next)$/i }).first().click({ force: true });
      await settle(page, 2000);
    }
    await page.keyboard.press('Escape');
  }

  if (want('mobile')) {
    const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, colorScheme: 'light', isMobile: true, hasTouch: true });
    await m.addCookies(await ctx.cookies());
    const mp = await m.newPage();
    await login(mp);
    await mp.goto(BASE + '/lovelace/family-room');
    await settle(mp, 4000);
    await mp.screenshot({ path: `${OUT}/dashboard-mobile.png` });
    console.log('mobile');
  }

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
