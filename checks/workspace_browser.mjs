import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), 'lims-workspace-'));
const artifacts = join(root, 'var/browser-artifacts', `workspace-${Date.now()}`);
const env = {...process.env, DJANGO_SETTINGS_MODULE: 'config.local',
  LIMS_LOCAL_DB: join(scratch, 'test.sqlite3'),
  LIMS_LOCAL_MEDIA: join(scratch, 'media'), PYTHONUNBUFFERED: '1'};
delete env.DATABASE_URL;
const python = env.LIMS_PYTHON || join(root, '.venv/bin/python');
let server, browser, page, logs = '';
mkdirSync(artifacts, {recursive: true});

function command(args) {
  const result = spawnSync(python, args, {cwd: root, env, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
  return result.stdout;
}

async function startServer() {
  const code = `from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer,make_server
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
class Server(ThreadingMixIn,WSGIServer): daemon_threads=True
with make_server('127.0.0.1',0,StaticFilesHandler(get_wsgi_application()),server_class=Server) as server:
 print('WORKSPACE_READY:'+str(server.server_port),flush=True)
 server.serve_forever()`;
  server = spawn(python, ['-c', code], {cwd: root, env});
  server.stderr.on('data', data => { logs = (logs + data).slice(-18000); });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error(logs || 'Server timeout')), 15000);
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`Server exited ${code}: ${logs}`)));
    server.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/WORKSPACE_READY:(\d+)/);
      if (match) { clearTimeout(timer); resolve(`http://127.0.0.1:${match[1]}`); }
    });
  });
}

function fixture() {
  return JSON.parse(command(['-c', `import django,json;django.setup()
from lab.models import TestSession,MeasurementGroup,SourceCell,EmployeeProfile
employee=EmployeeProfile.objects.get(full_name='Гущин Даниил')
card=TestSession.objects.create(identifier='UI-compact',title='Проверка рабочего листа',status='active',created_by=employee)
group=MeasurementGroup.objects.get(name='Основные параметры')
cell=SourceCell.objects.filter(sheet__file_id=1).first()
print(json.dumps({'card':card.pk,'group':group.pk,'sheet':cell.sheet_id,'cell':cell.pk,'address':cell.address}))`]).trim());
}

async function login(page, base) {
  await page.goto(base + '/login/');
  await page.locator('#id_password').fill('lims-demo');
  await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: /Продолжить/}).click()]);
  await page.locator('#id_employee').selectOption({label: 'Гущин Даниил'});
  await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: /Открыть лабораторию/}).click()]);
  if (await page.locator('#lab-assistant').isVisible()) {
    await page.locator('#assistant-close').click();
  }
}

async function checkFits(page, path) {
  await page.evaluate(() => new Promise(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  const geometry = await page.evaluate(() => ({
    width: innerWidth, height: innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
  }));
  assert.ok(geometry.scrollWidth <= geometry.width + 1, `${path}: horizontal ${JSON.stringify(geometry)}`);
  assert.ok(geometry.scrollHeight <= geometry.height + 1, `${path}: vertical ${JSON.stringify(geometry)}`);
}

async function checkRegisters(page, base) {
  for (const path of ['/', '/recipes/', '/experiments/', '/materials/', '/lots/',
    '/material-categories/', '/mixtures/', '/metrics/', '/sources/',
    '/measurement-groups/', '/history/']) {
    assert.equal((await page.goto(base + path, {waitUntil: 'networkidle'})).status(), 200);
    await checkFits(page, path);
    await page.screenshot({path: join(artifacts, `${path.replaceAll('/', '') || 'overview'}.png`)});
  }
  await page.goto(base + '/materials/', {waitUntil: 'networkidle'});
  const search = page.getByRole('textbox', {name: 'Поиск материалов'});
  await search.fill('Inovol');
  await page.waitForFunction(() => location.search.includes('q=Inovol'));
  assert.ok(await page.locator('tbody tr').count() > 0);
  await checkFits(page, 'live search');
  assert.equal(await page.locator('.workspace-scroll thead th').first()
    .evaluate(th => getComputedStyle(th).position), 'sticky');
}

async function checkVisibleSections(page, base) {
  for (const path of ['/', '/experiments/1/', '/materials/1/', '/lots/1/',
    '/material-categories/1/', '/mixtures/1/', '/measurement-groups/1/', '/sources/1/']) {
    await page.goto(base + path, {waitUntil: 'networkidle'});
    assert.equal(await page.getByRole('tab').count(), 0, path);
    assert.equal(await page.locator('[role=tabpanel], [data-workspace-tab]').count(), 0, path);
    const sections = page.locator('[data-workspace-section]');
    assert.ok(await sections.count() > 1, path);
    for (const section of await sections.all()) {
      assert.equal(await section.isVisible(), true, path);
    }
    if (path === '/' || path.startsWith('/measurement-groups/')) {
      await checkFits(page, path);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, path);
    const filename = path.replaceAll('/', '-') + '-sections.png';
    await page.screenshot({path: join(artifacts, filename), fullPage: true});
  }
}

async function checkMixtureSpace(page, base) {
  await page.goto(base + '/mixtures/2/', {waitUntil: 'networkidle'});
  await checkFits(page, 'mixture detail');
  const table = page.locator('#mixture-recipes .table-scroll');
  assert.ok(await table.locator('tbody tr').count() > 12);
  const area = await table.boundingBox();
  const main = await page.locator('#main-content').boundingBox();
  assert.ok(area.width >= (main.width - 40) * .95, 'Recipes use the available width');
  assert.ok(area.height >= 400, 'Recipes use the available height');
  assert.ok((await page.locator('#mixture-history').boundingBox()).height < 60,
    'Empty history stays compact');
  const count = await table.locator('tbody tr').count();
  await page.locator('[data-workspace-fullscreen]').click();
  await checkFits(page, 'mixture fullscreen');
  assert.ok((await table.boundingBox()).height > area.height);
  assert.equal(await table.locator('tbody tr').count(), count);
  await page.keyboard.press('Escape');
  await checkFits(page, 'mixture restored');
  await page.screenshot({path: join(artifacts, 'mixture-detail.png')});
}

async function checkFullscreen(page, base) {
  await page.setViewportSize({width: 1440, height: 900});
  await page.goto(base + '/materials/', {waitUntil: 'networkidle'});
  if (await page.locator('#lab-assistant').isVisible()) {
    await page.locator('#assistant-close').click();
  }
  await page.locator('#assistant-launcher').click();
  const expand = page.locator('[data-workspace-fullscreen]');
  await expand.click();
  assert.equal(await page.locator('.sidebar').isVisible(), false);
  assert.equal(await expand.getAttribute('aria-pressed'), 'true');
  await page.setViewportSize({width: 1366, height: 768});
  assert.equal(await page.locator('.main-shell').evaluate(node => node.inert), false);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.sidebar').isVisible(), true);
  assert.equal(await expand.getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('#lab-assistant').isVisible(), true);
  assert.equal(await page.locator('#assistant-close')
    .evaluate(node => node === document.activeElement), true);
  await page.locator('#assistant-close').click();
  await expand.focus();
  await expand.click();
  await page.keyboard.press('Escape');
  assert.equal(await expand.evaluate(node => node === document.activeElement), true);
}

async function checkSourceAnchor(page, base, info) {
  await page.goto(`${base}/sources/1/?sheet=${info.sheet}&cell=${info.address}#cell-${info.cell}`,
    {waitUntil: 'networkidle'});
  assert.equal(await page.locator('#source-fragments').isVisible(), true);
  const target = page.locator(`#cell-${info.cell}`);
  assert.equal(await target.isVisible(), true);
  await page.evaluate(id => { location.hash = `cell-${id}`; }, info.cell);
  await target.waitFor({state: 'visible'});
  assert.equal(await page.locator('#source-fragments').isVisible(), true);
  assert.equal(await page.locator('#source-provenance').isVisible(), true);
}

async function checkMeasurements(page, base, info) {
  const path = `/experiments/${info.card}/measurement-groups/new/?measurement_group=${info.group}`;
  await page.goto(base + path, {waitUntil: 'networkidle'});
  const inputs = page.locator('.measurement-table [data-measurement-value]');
  assert.equal(await inputs.count(), 2);
  await inputs.first().fill('0');
  await inputs.first().press('Enter');
  assert.equal(await inputs.nth(1).evaluate(node => node === document.activeElement), true);
  await inputs.nth(1).fill('55');
  await inputs.nth(1).press('Shift+Enter');
  assert.equal(await inputs.first().evaluate(node => node === document.activeElement), true);
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(await inputs.first().inputValue(), '0');
  await inputs.nth(1).fill('');
  await page.getByRole('button', {name: 'Сохранить группу измерений'}).click();
  await page.locator('.field-error').first().waitFor();
  assert.equal(await inputs.first().inputValue(), '0');
  const reasons = page.locator('.measurement-reason-cell input:visible');
  assert.equal(await reasons.count(), 1);
  await reasons.fill('Нет измерения');
  await Promise.all([page.waitForNavigation(),
    page.getByRole('button', {name: 'Сохранить группу измерений'}).click()]);
  assert.match(await page.locator('.experiment-results').innerText(), /Нет измерения/);
  await page.screenshot({path: join(artifacts, 'measurement-saved.png')});
}

async function checkResponsive(browser, page, base, info) {
  for (const viewport of [{width: 390, height: 844}, {width: 768, height: 1024}]) {
    const touch = await browser.newContext({viewport, hasTouch: true, isMobile: true,
      storageState: await page.context().storageState()});
    const mobile = await touch.newPage();
    for (const path of ['/', '/lots/', '/sources/1/', '/materials/1/', '/experiments/1/',
      `/experiments/${info.card}/measurement-groups/new/?measurement_group=${info.group}`]) {
      await mobile.goto(base + path, {waitUntil: 'networkidle'});
      assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, path);
    }
    for (const input of await mobile.locator('.measurement-table [data-measurement-value]').all()) {
      assert.ok((await input.boundingBox()).height >= 52);
      assert.ok(await input.evaluate(node => parseFloat(getComputedStyle(node).fontSize)) >= 16);
    }
    await mobile.screenshot({path: join(artifacts, `measurements-${viewport.width}.png`), fullPage: true});
    await touch.close();
  }
  const nojs = await browser.newContext({javaScriptEnabled: false,
    storageState: await page.context().storageState()});
  const fallback = await nojs.newPage();
  await fallback.goto(base + '/sources/1/');
  for (const panel of await fallback.locator('[data-workspace-section]').all()) {
    assert.equal(await panel.isVisible(), true);
  }
  assert.equal(await fallback.locator('[data-workspace-fullscreen]').isVisible(), false);
  await nojs.close();
}

try {
  command(['manage.py', 'migrate', '--noinput']);
  command(['manage.py', 'seed_initial']);
  const info = fixture();
  const base = await startServer();
  browser = await chromium.launch({headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  page = await browser.newPage({viewport: {width: 1366, height: 768}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page, base);
  await checkRegisters(page, base);
  console.log('Registers fit 1366 × 768; live search and sticky headers passed.');
  await checkVisibleSections(page, base);
  console.log('All record sections are visible simultaneously; no tabs or hidden panels.');
  await checkMixtureSpace(page, base);
  await checkFullscreen(page, base);
  await checkSourceAnchor(page, base, info);
  console.log('Fullscreen with an open assistant and direct source-cell links passed.');
  await checkMeasurements(page, base, info);
  await checkResponsive(browser, page, base, info);
  assert.deepEqual(errors, []);
  console.log(`PASS: compact desktop, live registries, all sections visible, keyboard, hash links, fullscreen, measurement drafts/validation/save, responsive and no-JS. Artifacts: ${artifacts}`);
} catch (error) {
  console.error(error);
  await page?.screenshot({path: join(artifacts, 'failure.png'), fullPage: true}).catch(() => {});
  console.error(`Artifacts: ${artifacts}`);
  if (logs.includes('Traceback')) console.error(logs);
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
