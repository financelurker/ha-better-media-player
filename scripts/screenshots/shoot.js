// Take README screenshots from the local demo instance.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8123';
const OUT = process.argv[2] || 'out';
const ONLY = process.argv[3] ? process.argv[3].split(',') : null;  // default: all but dashboard-music
const session = JSON.parse(fs.readFileSync('session.json', 'utf8'));
fs.mkdirSync(OUT, { recursive: true });

const want = (name) => (ONLY ? ONLY.includes(name) : name !== 'dashboard-music');

async function login(page) {
  await page.goto(BASE + '/');
  await page.waitForSelector('input[name="username"], ha-auth-flow', { timeout: 30000 });
  await page.locator('input[name="username"]').fill('demo');
  await page.locator('input[name="password"]').fill('demo-pass-2026');
  await page.keyboard.press('Enter');
  await page.waitForURL((u) => !/auth/.test(u.toString()), { timeout: 30000 }); await settle(page, 3000);
}

async function settle(page, ms = 2500) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForSelector('home-assistant-main', { timeout: 60000 }).catch(() => console.log('no main'));
  await page.waitForTimeout(ms);
}

async function dashboard(page, path = '/dashboard-rooms/family-room') {
  await page.goto(BASE + path);
  await page.locator('hui-media-control-card').first().waitFor({ timeout: 90000 });
  await settle(page, 3000);
}

async function moreInfo(page, entityId) {
  await page.evaluate((id) => {
    const ha = document.querySelector('home-assistant');
    ha.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId: id }, bubbles: true, composed: true }));
  }, entityId);
  await settle(page, 2000);
}

const DIALOGS = ['ha-dialog .mdc-dialog__surface', 'ha-md-dialog', 'wa-dialog [part="dialog"]', 'ha-adaptive-dialog', 'ha-dialog'];

async function dialogBox(page) {
  for (const sel of DIALOGS) {
    for (const loc of await page.locator(sel).all()) {
      const box = await loc.boundingBox();
      if (box && box.width > 100 && box.height > 100) return box;
    }
  }
  return null;
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
    await dashboard(page);
    await page.screenshot({ path: `${OUT}/dashboard.png` });
    console.log('dashboard');
  }

  if (want('dashboard-music')) {  // after: python scene.py music
    await dashboard(page);
    await page.screenshot({ path: `${OUT}/dashboard-music.png` });
    console.log('dashboard-music');
  }

  if (want('more-info')) {
    await page.setViewportSize({ width: 1400, height: 1000 });
    await dashboard(page);
    await moreInfo(page, 'media_player.family_room');
    await shotDialog(page, 'more-info.png');
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1400, height: 640 });
  }

  if (want('handover')) {
    // the room's select "Hand over to", open: the rooms that can take over what plays
    await page.setViewportSize({ width: 1400, height: 1000 });
    await dashboard(page);
    await moreInfo(page, 'select.family_room_hand_over_to');
    await page.locator('ha-control-select-menu, ha-select, ha-md-select').first().click();
    await page.waitForTimeout(1500);
    const dialog = await dialogBox(page);
    const last = await page.getByText('Guest Room', { exact: true }).last().boundingBox();
    await page.screenshot({
      path: `${OUT}/handover.png`,
      clip: { x: dialog.x, y: dialog.y, width: dialog.width, height: last.y + last.height + 12 - dialog.y },
    });
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1400, height: 640 });
    console.log('handover');
  }

  if (want('announce')) {
    // the announce action in the developer tools (filled in YAML mode, shown in UI mode)
    await page.setViewportSize({ width: 1100, height: 1800 });
    await page.goto(BASE + '/developer-tools/action');
    await settle(page, 3000);
    await page.getByText('YAML mode', { exact: true }).click();
    await page.waitForTimeout(1500);
    await page.locator('.cm-content').first().click();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Delete');
    const yaml = [
      'action: better_media_player.announce',
      'target:',
      '  entity_id:',
      '    - media_player.family_room',
      '    - media_player.bedroom_2',
      'data:',
      '  message: Dinner is ready',
      '  volume: normal',
    ];
    for (const line of yaml) {  // (no auto-indent: each line starts at column 0)
      await page.keyboard.type(line);
      await page.keyboard.press('Enter');
      await page.keyboard.press('Shift+Home');
      await page.keyboard.press('Delete');
    }
    await page.getByText('UI mode', { exact: true }).click();
    await page.waitForTimeout(3000);
    await page.locator('ha-card').filter({ hasText: 'Perform action' }).first().screenshot({ path: `${OUT}/announce.png` });
    await page.setViewportSize({ width: 1400, height: 640 });
    console.log('announce');
  }

  if (want('device')) {
    await page.setViewportSize({ width: 1400, height: 1240 });
    await page.goto(`${BASE}/config/devices/device/${session.device_id}`);
    await settle(page, 4000);
    await page.screenshot({ path: `${OUT}/device.png` });
    await page.setViewportSize({ width: 1400, height: 640 });
    console.log('device');
  }

  if (want('device-settings')) {
    // the room's settings: the Configuration card of the device page, in full
    await page.setViewportSize({ width: 560, height: 4000 });  // phone layout: the card spans the width, full names
    await page.goto(`${BASE}/config/devices/device/${session.device_id}`);
    await settle(page, 4000);
    const card = page.locator('ha-card').filter({ hasText: 'Configuration' }).last();
    await card.screenshot({ path: `${OUT}/device-settings.png` });
    await page.setViewportSize({ width: 1400, height: 640 });
    console.log('device-settings');
  }

  if (want('options')) {
    await page.setViewportSize({ width: 1400, height: 1500 });
    await page.goto(`${BASE}/config/integrations/integration/better_media_player`);
    await settle(page, 3000);
    await page.screenshot({ path: `${OUT}/_integration.png` });
    // the cog / "Configure" button of the entry
    // (the Family Room entry; the entries are listed by name)
    const row = page.locator('ha-config-entry-row').filter({ hasText: 'Family Room' }).first();
    const btn = row.getByRole('button', { name: /configure/i }).first();
    if (await btn.count()) {
      await btn.click({ force: true });
    } else {
      await page.locator('ha-icon-button[label*="Configure" i], ha-button:has-text("Configure")').first().click();
    }
    await settle(page, 2500);
    await shotDialog(page, 'options-menu.png');
    for (const [label, file] of [['Media players', 'options-players.png'], ['Devices and volume', 'options-devices.png']]) {
      if (!(await page.getByText(label, { exact: true }).count())) {  // the volume note in between
        await page.getByRole('button', { name: /^(submit|next|ok)$/i }).first().click({ force: true });
        await settle(page, 2000);
      }
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
    await dashboard(mp);
    await mp.screenshot({ path: `${OUT}/dashboard-mobile.png` });
    console.log('mobile');
  }

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
