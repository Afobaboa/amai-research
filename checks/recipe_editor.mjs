import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), 'lims-recipe-editor-'));
const env = {...process.env, DJANGO_SETTINGS_MODULE: 'config.local',
  LIMS_LOCAL_DB: join(scratch, 'test.sqlite3'),
  LIMS_LOCAL_MEDIA: join(scratch, 'media'), PYTHONUNBUFFERED: '1'};
delete env.DATABASE_URL;
const python = env.LIMS_PYTHON || join(root, '.venv/bin/python');
const artifacts = join(root, 'var/browser-artifacts', `recipe-editor-${Date.now()}`);
mkdirSync(artifacts, {recursive: true});
let server, browser, page;
let serverLogs = '';

function command(args) {
  const result = spawnSync(python, args, {cwd: root, env, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
  return result.stdout;
}

function databaseInfo() {
  const code = `import django,json;django.setup()
from lab.models import RecipeVersion,Material,MaterialLot
r=RecipeVersion.objects.get(version_label='316.18')
m=Material.objects.create(name='QA replacement polyol')
l=MaterialLot.objects.create(material=m,lot_code='QA-REPLACE',density_g_ml=4,price_per_kg=100,price_currency='EUR')
print(json.dumps({'recipe':r.pk,'material':m.pk,'lot':l.pk}))`;
  return JSON.parse(command(['-c', code]).trim());
}

async function startServer() {
  const code = `import json
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer,make_server
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
class Server(ThreadingMixIn,WSGIServer): daemon_threads=True
with make_server('127.0.0.1',0,StaticFilesHandler(get_wsgi_application()),server_class=Server) as server:
 print('RECIPE_READY:'+str(server.server_port),flush=True)
 server.serve_forever()`;
  server = spawn(python, ['-c', code], {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe']});
  server.stderr.on('data', data => { serverLogs = (serverLogs + data).slice(-20000); });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(serverLogs || 'Server startup timeout')), 15000);
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`Server exited ${code}: ${serverLogs}`)));
    server.stdout.on('data', chunk => {
      const match = String(chunk).match(/RECIPE_READY:(\d+)/);
      if (match) { clearTimeout(timer); resolve(`http://127.0.0.1:${match[1]}`); }
    });
  });
}

function output(page, path) { return page.locator(`[data-recipe-output="${path}"]`); }

async function numeric(locator) {
  const raw = await locator.getAttribute('data-recipe-raw-value');
  if (raw !== null) return raw === '' ? NaN : Number(raw);
  const label = await locator.innerText();
  return Number(label.replace(/[^\d,.-]/g, '').replace(',', '.'));
}

async function settled(page) {
  try {
    await page.waitForFunction(() => {
      const form = document.querySelector('form[data-recipe-editor]');
      const status = document.querySelector('[data-recipe-preview-status]');
      return form?.getAttribute('aria-busy') === 'false'
        && status?.textContent.includes('Расчёт обновлён');
    });
  } catch (error) {
    console.error('Preview status:', await page.locator('[data-recipe-preview-status]').textContent());
    console.error('Scripts:', await page.locator('script[src]').evaluateAll(nodes => nodes.map(node => node.src)));
    throw error;
  }
}

async function login(page, base) {
  await page.goto(base + '/login/');
  await page.locator('#id_password').fill('lims-demo');
  await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: /Продолжить/}).click()]);
  await page.locator('#id_employee').selectOption({label: 'Гущин Даниил'});
  await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: /Открыть лабораторию/}).click()]);
}

async function checkLiveCalculation(page) {
  await settled(page);
  assert.equal(await numeric(output(page, 'filling.polyol.mass_g')), 260.4);
  assert.equal(await numeric(output(page, 'filling.isocyanate.mass_g')), 377.2);
  assert.equal(await numeric(output(page, 'filling.mass_g')), 790.9);
  assert.equal(await numeric(output(page, 'filling.volume_ml')), 800);
  assert.ok(Math.abs(await numeric(output(page, 'filling.cost')) - 74.44431206) < 0.000001);
  assert.equal(await page.locator('input[name$="-mass_g"],input[name$="-volume_ml"]').count(), 0);
  const volume = page.locator('#id_settings-fill_volume_ml');
  await volume.fill('900');
  await settled(page);
  assert.notEqual(await numeric(output(page, 'filling.polyol.mass_g')), 260.4);
  await volume.fill('800');
  await settled(page);
  assert.equal(await numeric(output(page, 'filling.polyol.mass_g')), 260.4);
  assert.equal(await page.locator('#id_settings-iso_index').getAttribute('readonly'), '');
  await page.locator('#id_settings-iso_ratio_mode').selectOption('index');
  await page.locator('#id_settings-iso_index').fill('5');
  await settled(page);
  assert.equal(await page.locator('#id_settings-isocyanate_parts').getAttribute('readonly'), '');
  assert.ok(Number(await page.locator('#id_settings-isocyanate_parts').inputValue()) > 0);
  await page.locator('#id_settings-iso_ratio_mode').selectOption('parts');
  await page.locator('#id_settings-isocyanate_parts').fill('145');
  await settled(page);
}

async function checkReplacement(page, info, fork) {
  await page.goto(fork, {waitUntil: 'networkidle'});
  await settled(page);
  const index = await page.locator('#polyol-formset .formset-rows select[name$="-material"]')
    .evaluateAll(selects => selects.findIndex(select => select.selectedOptions[0]?.textContent.includes('TCPP')));
  assert.ok(index >= 0, 'TCPP row must be present');
  const prefix = `#id_polyol-${index}-`;
  await page.locator(prefix + 'material').selectOption(String(info.material));
  await page.locator(prefix + 'specified_lot').selectOption(String(info.lot));
  const density = page.locator(prefix + 'density_g_ml');
  const price = page.locator(prefix + 'price_per_kg');
  assert.equal(Number(await density.inputValue()), 4);
  assert.equal(Number(await price.inputValue()), 100);
  assert.equal(await density.getAttribute('readonly'), '');
  assert.equal(await price.getAttribute('readonly'), '');
  assert.equal(await page.locator(prefix + 'ohv').inputValue(), '');
  assert.equal(await page.locator(prefix + 'ohv_applicable').isChecked(), true);
  await page.locator(prefix + 'ohv').fill('112');
  await settled(page);
  const row = page.locator('#polyol-formset .formset-rows > .formset-row').nth(index);
  await row.locator('details').first().locator('summary').click();
  const mass = await numeric(row.locator('[data-recipe-row-output="mass_g"]'));
  const cost = await numeric(row.locator('[data-recipe-row-output="cost"]'));
  assert.ok(Math.abs(cost - mass / 1000 * 100) < 0.000001);
  const itemVolume = await numeric(row.locator('[data-recipe-row-output="volume_ml"]'));
  assert.ok(Math.abs(itemVolume - mass / 4) < 0.000001);
}

async function checkWorksheetLayout(page) {
  await checkDocumentOverflow(page, 1320, 1000);
  await page.getByRole('button', {name: 'На весь экран', exact: true}).click();
  await page.setViewportSize({width: 1366, height: 768});
  await settled(page);
  assert.equal(await page.locator('.sidebar').isVisible(), false);
  assert.equal(await page.locator('#lab-assistant').isVisible(), false);
  assertWorksheetBounds(await worksheetBounds(page));
  await checkTotalColumnAlignment(page, 'fullscreen 1366');
  for (const name of ['material', 'specified_lot', 'quantity', 'ohv',
    'density_g_ml', 'price_per_kg']) {
    assert.equal(await page.locator(`#id_polyol-0-${name}`)
      .locator('..').isVisible(), true);
  }
  await page.screenshot({path: join(artifacts, 'recipe-editor-worksheet.png'),
    fullPage: true});
  await checkExtraWorksheetRows(page);
  await checkInspectorAndKeyboard(page);
  await checkUnusedMiddleRows(page);
  await checkDocumentOverflow(page, 1101, 768);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.sidebar').isVisible(), true);
  assert.equal(await page.locator('[data-recipe-expand]')
    .getAttribute('aria-pressed'), 'false');
  await page.setViewportSize({width: 1440, height: 1000});
  await page.evaluate(() => scrollTo(0, 0));
  await checkTotalColumnAlignment(page, 'default 1440');
}

async function checkTotalColumnAlignment(page, viewport) {
  const columns = [...await gasTotalColumnBounds(page),
    ...await fillingTotalColumnBounds(page)];
  assert.equal(columns.length, 7);
  for (const column of columns) {
    assert.ok(Math.abs(column.total - column.row) <= 1,
      `${viewport}: footer vs first row ${JSON.stringify(column)}`);
    assert.ok(Math.abs(column.total - column.header) <= 1,
      `${viewport}: footer vs header ${JSON.stringify(column)}`);
  }
}

async function gasTotalColumnBounds(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('.recipe-gas-panel');
    const right = selector => {
      const element = panel.querySelector(selector);
      if (!element) throw new Error(`Missing gas column: ${selector}`);
      return element.getBoundingClientRect().right;
    };
    const columns = [['quantity', 'quantity', 2],
      ['density_g_ml', 'density', 3], ['pressure_bar', 'pressure', 5],
      ['mass_g', 'mass', 6]];
    return columns.map(([name, cell, heading]) => ({
      name: `gases.${name}`,
      total: right(`.recipe-gas-total [data-recipe-total-column="${name}"]`),
      row: right(`.formset-rows > .formset-row:first-child .recipe-cell-${cell}`),
      header: right(`.recipe-gas-columns > span:nth-child(${heading})`),
    }));
  });
}

async function fillingTotalColumnBounds(page) {
  return page.evaluate(() => {
    const table = document.querySelector('.recipe-filling-table');
    const right = selector => {
      const element = table.querySelector(selector);
      if (!element) throw new Error(`Missing filling column: ${selector}`);
      return element.getBoundingClientRect().right;
    };
    return ['mass_g', 'volume_ml', 'cost'].map((name, index) => ({
      name: `filling.${name}`,
      total: right(`tfoot .recipe-filling-total > td:nth-of-type(${index + 1})`),
      row: right(`tbody tr:first-child > td:nth-child(${index + 3})`),
      header: right(`thead tr > th:nth-child(${index + 3})`),
    }));
  });
}

async function worksheetBounds(page) {
  return page.evaluate(() => {
    const rectangle = selector => document.querySelector(selector)
      .getBoundingClientRect();
    return {
      polyolBottom: rectangle('.recipe-polyol-panel').bottom,
      polyolWidth: rectangle('.recipe-polyol-panel').width,
      iso: rectangle('.recipe-iso-panel').top,
      gasTop: rectangle('.recipe-gas-panel').top,
      totalTop: rectangle('.recipe-total-panel').top,
      total: rectangle('.recipe-total-panel').bottom,
      gas: rectangle('.recipe-gas-panel').bottom,
      width: document.documentElement.scrollWidth,
    };
  });
}

function assertWorksheetBounds(bounds) {
  assert.ok(bounds.iso >= bounds.polyolBottom, JSON.stringify(bounds));
  assert.ok(Math.abs(bounds.iso - bounds.gasTop) < 1, JSON.stringify(bounds));
  assert.ok(Math.abs(bounds.iso - bounds.totalTop) < 1, JSON.stringify(bounds));
  assert.ok(bounds.polyolWidth > 1300, JSON.stringify(bounds));
  assert.ok(bounds.total < 768 && bounds.gas < 768, JSON.stringify(bounds));
  assert.ok(bounds.width <= 1366, JSON.stringify(bounds));
}

async function checkDocumentOverflow(page, width, height) {
  await page.setViewportSize({width, height});
  await settled(page);
  const documentWidth = await page.evaluate(() => (
    document.documentElement.scrollWidth
  ));
  assert.ok(documentWidth <= width,
    `Document width ${documentWidth} exceeds viewport ${width}`);
}

async function checkInspectorAndKeyboard(page) {
  await checkInspectorSwitching(page);
  await page.locator('#id_polyol-0-quantity').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#id_polyol-1-quantity')
    .evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Shift+Enter');
  assert.equal(await page.locator('#id_polyol-0-quantity')
    .evaluate(node => node === document.activeElement), true);
  assert.equal(await page.locator('[data-recipe-filling-rows] tr').count(), 5);
  assert.equal(await page.locator('[data-recipe-filling-rows] th[scope="row"]')
    .count(), 5);
  const cost = output(page, 'filling.cost');
  assert.match(await cost.innerText(), /^74,444$/);
  assert.ok(Math.abs(await numeric(cost) - 74.44431206) < 0.000001);
  assert.match(await cost.getAttribute('title'), /74,444312/);
  assert.ok(Number(await page.locator('#id_settings-iso_index')
    .getAttribute('data-recipe-raw-value')) > 4.9);
}

async function checkInspectorSwitching(page) {
  const rows = page.locator('#polyol-formset .formset-rows > .formset-row');
  await rows.first().locator('summary').click();
  assert.equal(await rows.first().locator('.recipe-inspector').isVisible(), true);
  await rows.nth(1).locator('summary').focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => (
    document.querySelectorAll('.recipe-row-extra[open]').length === 1
      && document.querySelector('.recipe-inspector-active')
        ?.dataset.formPrefix === 'polyol-1'
  ));
  assert.match(await rows.nth(1).locator('[data-recipe-inspector-title]')
    .innerText(), /YD-C310/);
  await rows.nth(1).locator('.recipe-cell-material .search-select-trigger')
    .click();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.recipe-row-extra[open]').count(), 1);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.recipe-row-extra[open]').count(), 0);
  assert.equal(await page.locator('.recipe-inspector-active').count(), 0);
  assert.equal(await rows.nth(1).locator('summary')
    .evaluate(node => node === document.activeElement), true);
  assert.equal(await page.locator('[data-recipe-expand]')
    .getAttribute('aria-pressed'), 'true');
}

async function calculationSnapshot(page) {
  const paths = ['polyol.quantity_total', 'gases.quantity_total',
    'filling.polyol.mass_g', 'filling.isocyanate.mass_g',
    'filling.mass_g', 'filling.volume_ml', 'filling.cost'];
  return page.evaluate(paths => Object.fromEntries(paths.map(path => [
    path, document.querySelector(`[data-recipe-output="${path}"]`)
      ?.getAttribute('data-recipe-raw-value'),
  ])), paths);
}

async function checkUnusedMiddleRows(page) {
  const original = await calculationSnapshot(page);
  assert.equal(Number(original['polyol.quantity_total']), 100);
  assert.equal(Number(original['gases.quantity_total']), 100);
  for (const prefix of ['polyol', 'gases']) {
    await checkZeroRowAfterGap(page, prefix, original);
  }
}

async function checkZeroRowAfterGap(page, prefix, original) {
  const rows = page.locator(`#${prefix}-formset .formset-rows > .formset-row`);
  const count = await rows.count();
  assert.equal(await rows.last().locator('input[name$="-quantity"]')
    .inputValue(), '');
  await page.locator(`[data-add-form="${prefix}-formset"]`).click();
  const row = rows.last();
  await row.locator('input[name$="-quantity"]').fill('0');
  await settled(page);
  assert.equal(await rows.count(), count + 1);
  assert.equal(await numeric(row.locator('[data-recipe-row-output="mass_g"]')), 0);
  assert.equal(await numeric(row.locator('[data-recipe-row-output="cost"]')), 0);
  assert.deepEqual(await calculationSnapshot(page), original);
  assert.equal(await page.locator('[data-recipe-balance]')
    .getAttribute('data-state'), 'balanced');
  await row.getByRole('button', {name: 'Удалить строку', exact: true}).click();
  await settled(page);
  assert.equal(await rows.count(), count);
  assert.deepEqual(await calculationSnapshot(page), original);
}

async function checkExtraWorksheetRows(page) {
  const rows = page.locator('#polyol-formset .formset-rows > .formset-row');
  const original = await rows.count();
  for (let index = 0; index < 3; index++) {
    await page.getByRole('button', {name: '+ Добавить компонент', exact: true}).click();
  }
  await settled(page);
  assert.equal(await rows.count(), original + 3);
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
  }));
  assert.ok(size.width <= 1366 && size.height <= 768, JSON.stringify(size));
  await page.screenshot({path: join(artifacts, 'recipe-editor-extra-rows.png'), fullPage: true});
  for (let index = 0; index < 3; index++) {
    await rows.last().getByRole('button', {name: 'Удалить строку', exact: true}).click();
  }
  await settled(page);
  assert.equal(await rows.count(), original);
}

async function checkReasonsAndDrafts(page, fork) {
  await page.goto(fork, {waitUntil: 'networkidle'});
  await settled(page);
  await page.locator('#polyol-formset .formset-rows > .formset-row').first()
    .locator('details').first().locator('summary').click();
  const ohv = page.locator('#id_polyol-0-ohv');
  const reason = page.locator('#id_polyol-0-ohv_reason');
  await ohv.fill('');
  await settled(page);
  assert.equal(await reason.isVisible(), true);
  await page.locator('[data-recipe-issues-wrapper] > summary').click();
  assert.match(await page.locator('[data-recipe-preview-issues]').innerText(), /причин|OHV/i);
  await reason.fill('Нет паспорта исходной партии');
  await settled(page);
  await page.reload({waitUntil: 'networkidle'});
  await settled(page);
  assert.equal(await ohv.inputValue(), '');
  assert.equal(await reason.inputValue(), 'Нет паспорта исходной партии');
  await page.locator('#id_version_label').fill('QA-RECIPE-EDITOR-MISSING');
  await page.locator('#id_status').selectOption('draft');
  await Promise.all([page.waitForNavigation({waitUntil: 'networkidle'}), page.getByRole('button', {name: 'Сохранить рецептуру', exact: true}).click()]);
  assert.equal(await page.locator('.errorlist,.field-error').count(), 0,
    (await page.locator('.errorlist,.field-error').allTextContents()).join('; '));
  assert.match(new URL(page.url()).pathname, /^\/recipes\/\d+\/$/);
}

async function checkMobile(page, fork) {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto(fork, {waitUntil: 'networkidle'});
  await settled(page);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await page.getByRole('heading', {name: 'Состав полиольной смеси', exact: true}).isVisible(), true);
  await page.screenshot({path: join(artifacts, 'recipe-editor-mobile.png'), fullPage: true});
}

try {
  const existingOnly = process.argv.includes('--existing-only');
  if (!existingOnly) {
    command(['manage.py', 'migrate', '--noinput']);
    command(['manage.py', 'seed_initial']);
  }
  const info = existingOnly ? JSON.parse(command(['-c',
    "import sqlite3,json; c=sqlite3.connect('file:var/local.sqlite3?mode=ro',uri=True); print(json.dumps({'recipe':c.execute(\"SELECT id FROM lab_recipeversion WHERE version_label='316.18' ORDER BY id LIMIT 1\").fetchone()[0]}))"]).trim()) : databaseInfo();
  const base = existingOnly ? 'http://127.0.0.1:8001' : await startServer();
  const fork = `${base}/recipes/${info.recipe}/fork/`;
  browser = await chromium.launch({headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => { errors.push(error.message); console.error('Browser script error:', error.message); });
  page.on('console', message => { if (message.type() === 'error') console.error('Browser console:', message.text()); });
  page.on('response', async response => {
    if (response.status() >= 400) {
      console.error('Preview HTTP error:', response.status(), (await response.text()).slice(0, 2000));
    }
  });
  await login(page, base);
  await page.goto(fork, {waitUntil: 'networkidle'});
  await checkLiveCalculation(page);
  await checkWorksheetLayout(page);
  await page.screenshot({path: join(artifacts, 'recipe-editor-desktop.png'), fullPage: true});
  if (existingOnly) {
    await checkMobile(page, fork);
    assert.deepEqual(errors, []);
    console.log(`PASS: existing local preview, Excel 316.18, live calculations, readonly and mobile. Artifacts: ${artifacts}`);
  } else {
  await page.evaluate(() => sessionStorage.clear());
  await checkReplacement(page, info, fork);
  await page.evaluate(() => sessionStorage.clear());
  await checkReasonsAndDrafts(page, fork);
  await checkMobile(page, fork);
  assert.deepEqual(errors, []);
  console.log(`PASS: fresh seed, Excel 316.18, live preview, derived fields, material replacement, missing reasons, draft restoration, save and mobile. Artifacts: ${artifacts}`);
  }
} catch (error) {
  console.error(error);
  if (page) {
    await page.screenshot({path: join(artifacts, 'recipe-editor-failure.png'), fullPage: true}).catch(() => {});
    console.error('Browser artifacts:', artifacts);
  }
  if (serverLogs.includes('Traceback')) console.error(serverLogs);
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    const closed = new Promise(resolve => server.once('exit', resolve));
    server.kill('SIGTERM');
    await closed;
  }
  rmSync(scratch, {recursive: true, force: true});
}
