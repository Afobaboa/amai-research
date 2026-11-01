import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.KUDO_PLAYWRIGHT_MODULE || 'playwright');

// Cloud.ru is mocked at the browser boundary. This check never calls a model
// and keeps all laboratory writes in an isolated, disposable SQLite database.
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), 'lims-assistant-browser-'));
const python = process.env.LIMS_PYTHON || join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const env = {...process.env, DJANGO_SETTINGS_MODULE:'config.local', LIMS_LOCAL_DB:join(scratch, 'test.sqlite3'), LIMS_LOCAL_MEDIA:join(scratch, 'media'), PYTHONUNBUFFERED:'1'};
delete env.DATABASE_URL;
delete env.SITE_PASSWORD_HASH;
for (const key of Object.keys(env)) if (key.startsWith('CLOUD_RU_') || key.startsWith('ASSISTANT_')) delete env[key];
const artifacts = join(root, 'var', 'assistant-browser-artifacts');
mkdirSync(artifacts, {recursive:true});
let server, browser, stopped;
try {
  for (const args of [['migrate','--noinput'], ['seed_demo','--confirm-demo']]) {
    const run = spawnSync(python, ['manage.py', ...args], {cwd:root, env, encoding:'utf8'});
    assert.equal(run.status, 0, run.stderr + run.stdout);
  }
  const code = `
from wsgiref.simple_server import WSGIServer, make_server
from socketserver import ThreadingMixIn
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True
with make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), server_class=Server) as server:
    print('ASSISTANT_READY:' + str(server.server_port), flush=True)
    server.serve_forever()
`;
  server = spawn(python, ['-c', code], {cwd:root, env, stdio:['ignore','pipe','pipe']});
  stopped = new Promise(resolve => server.once('close', resolve));
  const base = await new Promise((resolve, reject) => {
    let output = '', errors = '';
    const timer = setTimeout(() => reject(new Error('Test server startup timeout: ' + errors)), 15000);
    server.stdout.on('data', data => {
      output += data;
      const match = output.match(/ASSISTANT_READY:(\d+)/);
      if (match) { clearTimeout(timer); resolve(`http://127.0.0.1:${match[1]}`); }
    });
    server.stderr.on('data', data => { errors = (errors + data).slice(-6000); });
    server.on('error', error => { clearTimeout(timer); reject(error); });
    server.on('exit', status => { clearTimeout(timer); reject(new Error(`Test server exited ${status}: ${errors}`)); });
  });
  browser = await chromium.launch({headless:!process.argv.includes('--headed'),args:['--no-sandbox','--disable-dev-shm-usage']});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let configured = true, mode = 'success', captured = [], pendingResolve;
  await page.route('**/assistant/status/', route => route.fulfill({json:{configured,model:'deepseek-test'}}));
  await page.route('**/assistant/chat/', async route => {
    captured.push(route.request().postDataJSON());
    assert.ok(route.request().headers()['x-csrftoken']);
    if (mode === 'pending') await new Promise(resolve => { pendingResolve = resolve; });
    try {
      if (mode === 'error') await route.fulfill({status:503,json:{error:'Cloud.ru временно недоступен.',code:'provider_unavailable'}});
      else await route.fulfill({json:{reply:'## Подтверждённые данные\n\nСмотрите **рецептуру** [DEMO](/recipes/1/).\n\n<img src=x onerror="window.injected=true">\n[danger](javascript:alert(1))',sources:[{title:'DEMO-316',url:'/recipes/1/'},{title:'Небезопасная ссылка',url:'javascript:alert(1)'}]}});
    } catch (error) { if (mode !== 'pending' && !String(error).includes('closed')) throw error; }
  });
  await page.goto(base + '/login/');
  assert.equal(await page.locator('#lab-assistant').count(), 0);
  await page.locator('#id_password').fill('lims-demo');
  await page.getByRole('button',{name:/Продолжить/}).click();
  assert.equal(await page.locator('#lab-assistant').count(), 0);
  const employeeValue = await page.locator('#id_employee option').evaluateAll(nodes => nodes.find(node => node.textContent.includes('Анна Волкова')).value);
  await page.locator('#id_employee').selectOption(employeeValue);
  await page.getByRole('button',{name:/Открыть лабораторию/}).click();
  await page.waitForLoadState('networkidle');
  await page.locator('#lab-assistant').waitFor({state:'visible'});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  for (const selector of ['.assistant-header','#assistant-form']) {
    const box = await page.locator(selector).boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height <= 1000, `${selector} must remain in the viewport`);
  }
  await page.screenshot({path:join(artifacts,'desktop.png'),fullPage:true});
  await page.locator('[data-assistant-prompt]').first().click();
  await page.locator('.assistant-message.is-assistant').waitFor();
  assert.equal(captured[0].page, '/');
  assert.equal(captured[0].messages[0].role, 'user');
  assert.equal(await page.locator('.assistant-message img').count(), 0);
  assert.equal(await page.locator('.assistant-message a[href^="javascript:"]').count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.locator('.assistant-sources a[href="/recipes/1/"]').count(), 1);
  for (const selector of ['.assistant-header','#assistant-form']) {
    const box = await page.locator(selector).boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height <= 1000, `${selector} stays visible after a reply`);
  }

  await page.locator('.assistant-sources a').click();
  await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('.assistant-message.is-assistant').count(), 1);
  await page.locator('#assistant-close').click();
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#lab-assistant').isVisible(), false);
  await page.locator('#assistant-launcher').click();
  mode = 'error';
  await page.locator('#assistant-input').fill('Проверь результаты');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.match(await page.locator('#assistant-error-text').textContent(), /Cloud.ru/);
  mode = 'success';
  await page.locator('#assistant-retry').click();
  await page.waitForFunction(() => document.querySelectorAll('.assistant-message.is-assistant').length === 2);
  assert.deepEqual(captured.at(-1).messages.map(item => item.role), ['user','assistant','user']);

  mode = 'pending';
  await page.locator('#assistant-input').fill('Долгий вопрос');
  await page.locator('#assistant-send').click();
  await page.waitForFunction(() => !document.getElementById('assistant-cancel').hidden);
  while (!pendingResolve) await new Promise(resolve => setTimeout(resolve, 25));
  await page.locator('#assistant-cancel').click();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.match(await page.locator('#assistant-error-text').textContent(), /остановлен/);
  pendingResolve(); pendingResolve = null;
  await page.locator('#assistant-new').click();
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(await page.locator('#assistant-welcome').isVisible(), true);
  mode = 'success';

  // Restored history is bounded by characters and preserves alternating roles.
  await page.evaluate(() => {
    const key = `kudo-assistant-v1:${document.getElementById('lab-assistant').dataset.employee}`;
    sessionStorage.setItem(key, JSON.stringify(Array.from({length:16}, (_, index) => ({role:index % 2 ? 'assistant':'user', content:'x'.repeat(11000)}))));
  });
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#assistant-input').fill('Короткий вопрос');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-pending').waitFor({state:'hidden'});
  assert.equal(await page.locator('.assistant-message').last().getAttribute('class'), 'assistant-message is-assistant');
  assert.ok(captured.at(-1).messages.reduce((sum,item) => sum + item.content.length, 0) <= 48000);
  assert.equal(captured.at(-1).messages[0].role,'user');

  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#lab-assistant').getAttribute('aria-modal'), 'true');
  assert.equal(await page.locator('.main-shell').evaluate(node => node.inert), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.locator('#assistant-new').click();
  await page.screenshot({path:join(artifacts,'mobile.png')});
  await page.locator('#assistant-close').focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.closest('#lab-assistant') !== null), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#lab-assistant').isVisible(), false);
  assert.equal(await page.locator('.main-shell').evaluate(node => node.inert), false);
  assert.equal(await page.locator('#assistant-launcher').evaluate(node => node === document.activeElement), true);
  configured = false;
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#assistant-launcher').click();
  assert.equal(await page.locator('#assistant-send').isDisabled(), true);
  assert.match(await page.locator('#assistant-status').textContent(), /ещё не настроено/);
  configured = true;
  await page.getByRole('button',{name:'Проверить настройки'}).click();
  await page.waitForFunction(() => !document.getElementById('assistant-send').disabled);
  assert.deepEqual(errors, []);
  console.log('Assistant browser checks passed: desktop/mobile, safe rendering, sources, navigation persistence, retry, cancellation, bounded history, keyboard/modal access and configuration.');
  console.log(`Screenshots: ${artifacts}`);
} finally {
  await browser?.close();
  if (server) {
    server.kill('SIGTERM');
    const timeout = setTimeout(() => server.kill('SIGKILL'), 3000);
    await stopped; clearTimeout(timeout);
  }
  rmSync(scratch, {recursive:true,force:true});
}
