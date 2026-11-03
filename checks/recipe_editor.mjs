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
  const mass = await numeric(row.locator('[data-recipe-row-output="mass_g"]'));
  const cost = await numeric(row.locator('[data-recipe-row-output="cost"]'));
  assert.ok(Math.abs(cost - mass / 1000 * 100) < 0.000001);
  const itemVolume = await numeric(row.locator('[data-recipe-row-output="volume_ml"]'));
  assert.ok(Math.abs(itemVolume - mass / 4) < 0.000001);
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
