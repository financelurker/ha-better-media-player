// Take README screenshots from the local demo instance.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8123';
const OUT = process.argv[2] || 'out';
const ONLY = process.argv[3] ? process.argv[3].split(',') : null;  // default: all but dashboard-music
const session = JSON.parse(fs.readFileSync('session.json', 'utf8'));
fs.mkdirSync(OUT, { recursive: true });

const want = (name) => (ONLY ? ONLY.includes(name) : !['dashboard-music', 'panel-live'].includes(name));

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

  if (want('mobile')) {
    const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, colorScheme: 'light', isMobile: true, hasTouch: true });
    await m.addCookies(await ctx.cookies());
    const mp = await m.newPage();
    await login(mp);
    await dashboard(mp);
    await mp.screenshot({ path: `${OUT}/dashboard-mobile.png` });
    console.log('mobile');
  }

  if (want('panel')) {
    // the configuration panel: the Family Room as cabled by cable_demo.py, the video's route shown
    await page.setViewportSize({ width: 1400, height: 760 });
    await page.goto(`${BASE}/better-media-player/${session.entry_id}`);
    await settle(page, 3000);
    const panel = page.locator('bmp-panel');
    await panel.getByRole('button', { name: 'Arrange automatically' }).click({ force: true });  // the places as the panel chooses them
    await page.waitForTimeout(800);
    await panel.locator('.chip', { hasText: 'Video' }).click();
    await page.waitForTimeout(500);
    await panel.screenshot({ path: `${OUT}/panel.png` });
    // a device's details (editing), on its own page width
    await page.setViewportSize({ width: 1400, height: 900 });
    await panel.locator('g.node', { hasText: 'Display' }).click();
    await page.waitForTimeout(600);
    await panel.screenshot({ path: `${OUT}/panel-device.png` });
    await panel.locator('.inspector .close').click();
    await page.setViewportSize({ width: 1400, height: 760 });
    await page.waitForTimeout(400);
    await panel.locator('.tabs button', { hasText: 'Room & behaviour' }).click();
    await page.waitForTimeout(800);
    await panel.screenshot({ path: `${OUT}/panel-settings.png` });
    await page.setViewportSize({ width: 1400, height: 640 });
    console.log('panel');
  }

  if (want('panel-live')) {  // after: python scene.py music (the music is paused here)
    // the live state: the music paused, its idle timeout counting down; a tile's details
    await page.setViewportSize({ width: 1400, height: 760 });
    await page.goto(`${BASE}/better-media-player/${session.entry_id}`);
    await settle(page, 2000);
    const panel = page.locator('bmp-panel');
    if (!(await panel.locator('.livesw input').isChecked())) await panel.locator('.livesw input').check();
    await page.evaluate(() => document.querySelector('home-assistant').hass.callService('media_player', 'media_pause', { entity_id: 'media_player.walkman' }));
    await page.waitForTimeout(14000);  // (a hold is shown once it lasted 10 s)
    await panel.locator('g.node', { hasText: 'Speakers' }).click();
    await page.waitForTimeout(800);
    await panel.screenshot({ path: `${OUT}/panel-live.png` });
    console.log('panel-live');
  }

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
