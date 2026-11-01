import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';

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
let server, browser, stopped, streamServer;
const streamSockets = new Set();
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
  // Unlike route.fulfill(), a real local HTTP fixture can keep an SSE response
  // open while the browser displays statuses and partial text. Tests decide
  // exactly when to send each chunk; no timing assumptions or model calls.
  let receiveStream;
  streamServer = createServer((request, response) => {
    response.setHeader('Access-Control-Allow-Origin', base);
    response.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-CSRFToken');
    response.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    if (request.method === 'OPTIONS') { response.writeHead(204); response.end(); return; }
    const chunks = [];
    request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => {
      if (!receiveStream) { response.writeHead(503); response.end(); return; }
      response.writeHead(200, {'Content-Type':'text/event-stream; charset=utf-8', 'Cache-Control':'no-cache'});
      response.flushHeaders();
      const receive = receiveStream;
      receiveStream = null;
      receive({request, response, payload:JSON.parse(Buffer.concat(chunks).toString('utf8'))});
    });
  });
  streamServer.on('connection', socket => {
    streamSockets.add(socket);
    socket.on('close', () => streamSockets.delete(socket));
  });
  await new Promise(resolve => streamServer.listen(0, '127.0.0.1', resolve));
  const streamUrl = `http://127.0.0.1:${streamServer.address().port}/stream/`;
  browser = await chromium.launch({headless:!process.argv.includes('--headed'),args:['--no-sandbox','--disable-dev-shm-usage']});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let configured = true, mode = 'success', captured = [], pendingResolve;
  const reply = [
    '## Подтверждённые данные',
    '',
    'Смотрите **рецептуру** [DEMO](/recipes/1/).',
    '',
    '| **Рецептура** | Значение | Комментарий |',
    '| :--- | ---: | :---: |',
    String.raw`| [DEMO](/recipes/1/) | 316 | A\|B |`,
    '| `x|y` | 42 | <img src=x onerror="window.injected=true"> |',
    '',
    'Имя | Итог',
    '--- | ---',
    'Без внешних разделителей | Готово',
    '',
    '| ' + Array.from({length:10}, (_, index) => `Длинный показатель ${index + 1}`).join(' | ') + ' |',
    '| ' + Array(10).fill('---').join(' | ') + ' |',
    '| ' + Array.from({length:10}, (_, index) => `Результат ${index + 1}`).join(' | ') + ' |',
    '',
    'Обычный текст | без таблицы.',
    '',
    '```text',
    '| Это | Код |',
    '| --- | --- |',
    '| A | B |',
    '```',
    '',
    '<img src=x onerror="window.injected=true">',
    '[danger](javascript:alert(1))'
  ].join('\n');
  await page.route('**/assistant/status/', route => route.fulfill({json:{configured,model:'deepseek-test'}}));
  await page.route('**/assistant/chat/', async route => {
    captured.push(route.request().postDataJSON());
    assert.ok(route.request().headers()['x-csrftoken']);
    if (mode === 'pending') await new Promise(resolve => { pendingResolve = resolve; });
    try {
      if (mode === 'error') await route.fulfill({status:503,json:{error:'Cloud.ru временно недоступен.',code:'provider_unavailable'}});
      else if (mode === 'history') await route.fulfill({json:{reply:'Запрос принят.',sources:[]}});
      else await route.fulfill({json:{reply,sources:[{title:'DEMO-316',url:'/recipes/1/'},{title:'Небезопасная ссылка',url:'javascript:alert(1)'}]}});
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
  assert.equal(Object.hasOwn(captured[0], 'form_state'), false, 'Pages without a form do not attach a draft');
  assert.equal(await page.locator('.assistant-message img').count(), 0);
  assert.equal(await page.locator('.assistant-message a[href^="javascript:"]').count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.locator('.assistant-sources a[href="/recipes/1/"]').count(), 1);
  const firstReply = page.locator('.assistant-message.is-assistant').first();
  const tables = firstReply.locator('.assistant-table-scroll > table');
  assert.equal(await tables.count(), 3, 'Only Markdown tables outside code fences become tables');
  const table = tables.first();
  assert.equal(await table.locator('thead th[scope="col"]').count(), 3);
  assert.equal(await table.locator('thead th strong').textContent(), 'Рецептура');
  assert.equal(await table.locator('tbody tr').count(), 2);
  assert.equal(await table.locator('tbody tr').first().locator('td').count(), 3);
  assert.equal(await table.locator('tbody tr').first().locator('td').nth(2).textContent(), 'A|B');
  assert.equal(await table.locator('td code').textContent(), 'x|y');
  assert.equal(await table.locator('td a[href="/recipes/1/"]').count(), 1);
  assert.deepEqual(await table.locator('thead th').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).textAlign)), ['left','right','center']);
  assert.deepEqual(await table.locator('tbody tr').first().locator('td').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).textAlign)), ['left','right','center']);
  assert.equal(await tables.nth(1).locator('tbody td').first().textContent(), 'Без внешних разделителей');
  assert.match(await firstReply.locator('pre code').textContent(), /\| Это \| Код \|/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  for (const selector of ['.assistant-header','#assistant-form']) {
    const box = await page.locator(selector).boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height <= 1000, `${selector} stays visible after a reply`);
  }

  const panel = page.locator('#lab-assistant'), resize = page.locator('#assistant-resize');
  const panelWidth = async () => (await panel.boundingBox()).width;
  const checkWidth = async expected => {
    await page.waitForFunction(width => Math.abs(document.getElementById('lab-assistant').getBoundingClientRect().width - width) <= 1, expected);
    assert.ok(Math.abs(await panelWidth() - expected) <= 1, `Panel width should be ${expected}`);
  };
  assert.equal(await resize.getAttribute('role'), 'separator');
  assert.equal(await resize.getAttribute('aria-orientation'), 'vertical');
  const initialWidth = await panelWidth();
  const grip = await resize.boundingBox();
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(grip.x + grip.width / 2 - 140, grip.y + grip.height / 2, {steps:10});
  await page.mouse.up();
  await checkWidth(initialWidth + 140);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.ok(Math.abs(await page.locator('.main-shell').evaluate(node => parseFloat(getComputedStyle(node).marginRight)) - await panelWidth()) <= 1, 'The desktop workspace follows the resized panel');
  await resize.focus();
  const draggedWidth = await panelWidth();
  await page.keyboard.press('ArrowLeft');
  assert.ok(await panelWidth() > draggedWidth, 'Left arrow expands the panel');
  await page.keyboard.press('ArrowRight');
  await checkWidth(draggedWidth);
  await page.keyboard.press('Home');
  await checkWidth(Number(await resize.getAttribute('aria-valuemin')));
  await page.keyboard.press('End');
  await checkWidth(Number(await resize.getAttribute('aria-valuemax')));
  assert.equal(Number(await resize.getAttribute('aria-valuenow')), Math.round(await panelWidth()));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'The dashboard fits beside the maximum-width panel');
  const savedWidth = await panelWidth();
  await page.screenshot({path:join(artifacts,'desktop-tables-resized.png'),fullPage:true});

  await page.locator('.assistant-sources a').click();
  await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('.assistant-message.is-assistant').count(), 1);
  await checkWidth(savedWidth);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Recipe details fit beside the maximum-width panel');
  assert.equal(await page.locator('.assistant-table-scroll > table').count(), 3, 'Tables survive history restoration after navigation');
  for (const path of ['/recipes/','/tests/','/recipes/new/','/recipes/1/']) {
    const response = await page.goto(base + path, {waitUntil:'networkidle'});
    assert.ok(response.ok(), `Desktop layout check opens ${path}`);
    await checkWidth(savedWidth);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${path} fits beside the maximum-width panel`);
  }
  await page.locator('#assistant-close').click();
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#lab-assistant').isVisible(), false);
  await page.locator('#assistant-launcher').click();
  await checkWidth(savedWidth);
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

  await page.setViewportSize({width:820,height:1000});
  assert.equal(await panel.getAttribute('aria-modal'), 'true');
  assert.equal(await resize.isVisible(), true);
  await resize.focus();
  await page.keyboard.press('End');
  await checkWidth(Number(await resize.getAttribute('aria-valuemax')));
  const tabletWidth = await panelWidth();
  await page.setViewportSize({width:700,height:1000});
  await page.waitForFunction(width => document.getElementById('lab-assistant').getBoundingClientRect().width < width, tabletWidth);
  assert.ok(await panelWidth() < tabletWidth, 'Narrower viewports clamp the preferred panel width');
  await checkWidth(Number(await resize.getAttribute('aria-valuemax')));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({width:820,height:1000});
  await checkWidth(tabletWidth);
  await page.screenshot({path:join(artifacts,'tablet-tables.png')});

  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#lab-assistant').getAttribute('aria-modal'), 'true');
  assert.equal(await resize.isVisible(), false);
  await checkWidth(390);
  assert.equal(await page.locator('.main-shell').evaluate(node => node.inert), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const wideTable = page.locator('.assistant-message.is-assistant').last().locator('.assistant-table-scroll').last();
  assert.equal(await wideTable.evaluate(node => node.scrollWidth > node.clientWidth), true, 'Wide tables scroll inside the chat on mobile');
  await wideTable.evaluate(node => { node.scrollLeft = 100; });
  assert.ok(await wideTable.evaluate(node => node.scrollLeft) > 0);
  await wideTable.scrollIntoViewIfNeeded();
  await page.screenshot({path:join(artifacts,'mobile-tables.png')});
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

  // The model sees the current, unsaved form values, including dynamic rows.
  // Capturing a draft must not submit the laboratory form or persist its values.
  await page.setViewportSize({width:1440,height:1000});
  await page.goto(base + '/recipes/new/', {waitUntil:'networkidle'});
  await page.locator('#assistant-new').click();
  const beforeEditing = captured.length;
  const draftTitle = 'НЕСОХРАНЁННАЯ QA рецептура';
  await page.locator('#id_title').fill(draftTitle);
  await page.locator('#id_version_label').fill('QA-DRAFT-1');
  await page.locator('#id_components-0-quantity_value').fill('0');
  const material = page.locator('#id_components-0-material');
  const materialValue = await material.locator('option').evaluateAll(nodes => nodes.find(node => node.textContent.includes('Полиол P-101')).value);
  await material.selectOption(materialValue);
  const materialLabel = await material.locator('option:checked').textContent();
  await page.locator('[data-add-form="components-formset"]').click();
  await page.locator('#id_components-2-quantity_value').fill('1.5');
  await page.locator('#id_components-2-material').selectOption(materialValue);
  await page.evaluate(() => {
    const form = document.getElementById('id_title').form;
    for (const [type, name] of [['hidden','qa_hidden'],['password','qa_password'],['file','qa_file'],['text','qa_disabled'],['text','qa_api_key']]) {
      const node = document.createElement('input');
      node.type = type; node.name = name;
      if (type !== 'file') node.value = 'SHOULD-NOT-BE-SENT';
      node.disabled = name === 'qa_disabled';
      form.append(node);
    }
    for (const checked of [true, false]) {
      const node = document.createElement('input');
      node.type = 'checkbox'; node.name = checked ? 'qa_checked' : 'qa_unchecked'; node.checked = checked;
      form.append(node);
    }
    const multiple = document.createElement('select');
    multiple.name = 'qa_multiple'; multiple.multiple = true;
    multiple.add(new Option('Первый вариант', 'first', false, true));
    multiple.add(new Option('Второй вариант', 'second', false, true));
    form.append(multiple);
  });
  assert.equal(captured.length, beforeEditing, 'Editing a form does not send background assistant requests');
  mode = 'error';
  await page.locator('#assistant-input').fill('Проверь заполнение текущей формы');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  const firstDraft = captured.at(-1);
  assert.equal(firstDraft.page, '/recipes/new/');
  assert.match(firstDraft.form_state.title, /рецептур/i);
  const draftFields = firstDraft.form_state.forms.flatMap(form => form.fields);
  const draftField = name => {
    const field = draftFields.find(field => field.name === name);
    assert.ok(field, `Current form context includes ${name}`);
    return field;
  };
  assert.equal(draftField('title').value, draftTitle);
  assert.equal(draftField('title').label, 'Название');
  assert.equal(draftField('title').section, 'Основная информация');
  assert.equal(draftField('title').changed, true);
  assert.equal(draftField('version_label').value, 'QA-DRAFT-1');
  assert.equal(draftField('components-0-quantity_value').value, '0', 'Zero is preserved as an entered value');
  assert.equal(draftField('components-1-quantity_value').value, '', 'An empty field remains empty');
  assert.equal(draftField('components-0-material').value, materialValue);
  assert.equal(draftField('components-0-material').display, materialLabel.trim());
  assert.equal(draftField('components-0-material').section, 'Состав рецептуры');
  assert.equal(draftField('components-2-quantity_value').value, '1.5');
  assert.equal(draftField('qa_checked').value, true);
  assert.equal(draftField('qa_unchecked').value, false);
  assert.deepEqual(draftField('qa_multiple').value, ['first','second']);
  assert.deepEqual(draftField('qa_multiple').display, ['Первый вариант','Второй вариант']);
  for (const name of ['csrfmiddlewaretoken','recipe_template','components-TOTAL_FORMS','qa_hidden','qa_password','qa_file','qa_disabled','qa_api_key']) {
    assert.equal(draftFields.some(field => field.name === name), false, `${name} must not enter the form snapshot`);
  }
  assert.equal(JSON.stringify(firstDraft).includes('SHOULD-NOT-BE-SENT'), false);
  assert.deepEqual(firstDraft.messages.map(message => Object.keys(message).sort()), [['content','role']], 'Form context stays outside conversation messages');
  const checkBackendDraft = async payload => {
    // APIRequestContext bypasses page mocks. With no provider key, a valid
    // real browser snapshot reaches configuration checking without an API call.
    const response = await page.request.post(base + '/assistant/chat/', {
      data:payload,
      headers:{'X-CSRFToken':await page.locator('#assistant-form [name="csrfmiddlewaretoken"]').inputValue(), Referer:page.url()},
    });
    assert.equal(response.status(), 503, await response.text());
    assert.equal((await response.json()).code, 'not_configured', 'The real endpoint accepts the browser snapshot');
  };
  await checkBackendDraft(firstDraft);

  const revisedTitle = 'ИЗМЕНЁННЫЙ QA черновик';
  await page.locator('#id_title').fill(revisedTitle);
  mode = 'success';
  await page.locator('#assistant-retry').click();
  await page.locator('.assistant-message.is-assistant').waitFor();
  assert.equal(captured.at(-1).form_state.forms.flatMap(form => form.fields).find(field => field.name === 'title').value, revisedTitle, 'Retry captures the latest form state');
  const savedHistory = await page.evaluate(() => sessionStorage.getItem(`kudo-assistant-v1:${document.getElementById('lab-assistant').dataset.employee}`));
  assert.equal(savedHistory.includes(draftTitle) || savedHistory.includes(revisedTitle), false, 'Unsaved form values are not persisted in chat history');
  assert.equal(savedHistory.includes('form_state'), false);
  assert.deepEqual(Object.keys(JSON.parse(savedHistory)[0]).sort(), ['content','role']);
  assert.equal(await page.locator('#id_title').inputValue(), revisedTitle, 'Asking the assistant leaves the laboratory form editable and unsaved');
  assert.equal(new URL(page.url()).pathname, '/recipes/new/');

  await page.evaluate(() => {
    const form = document.getElementById('id_title').form;
    const text = document.createElement('textarea');
    text.name = 'qa_long_text'; text.value = 'Я'.repeat(4000); form.append(text);
    for (let index = 0; index < 210; index++) {
      const node = document.createElement('input');
      node.name = `qa_extra_${index}`; node.value = 'Дополнительное поле'; form.append(node);
    }
  });
  await page.locator('#assistant-input').fill('Проверь форму ещё раз');
  await page.locator('#assistant-send').click();
  await page.waitForFunction(() => document.querySelectorAll('.assistant-message.is-assistant').length === 2);
  const boundedDraft = captured.at(-1).form_state;
  const boundedFields = boundedDraft.forms.flatMap(form => form.fields);
  assert.equal(boundedDraft.truncated, true, 'Truncated snapshots are explicitly marked');
  assert.ok(boundedFields.length <= 200 && boundedDraft.forms.length <= 3);
  assert.ok(JSON.stringify(boundedDraft).length <= 24000, 'Form context has a separate request budget');
  assert.ok(boundedFields.every(field => typeof field.value !== 'string' || field.value.length <= 2000));
  await checkBackendDraft(captured.at(-1));

  await page.goto(base + '/', {waitUntil:'networkidle'});
  await page.locator('#assistant-new').click();
  await page.locator('#lab-assistant').evaluate((node, url) => { node.dataset.chatUrl = url; }, streamUrl);
  const openStream = async (question, retry = false) => {
    let timer;
    const incoming = new Promise((resolve, reject) => {
      receiveStream = resolve;
      timer = setTimeout(() => { receiveStream = null; reject(new Error('Streaming fixture received no request')); }, 10000);
    });
    try {
      if (retry) await page.locator('#assistant-retry').click();
      else {
        await page.locator('#assistant-input').fill(question);
        await page.locator('#assistant-send').click();
      }
      const stream = await incoming;
      assert.match(stream.request.headers.accept, /text\/event-stream/);
      assert.ok(stream.request.headers['x-csrftoken']);
      captured.push(stream.payload);
      return stream.response;
    } finally { clearTimeout(timer); }
  };
  const event = (response, name, data) => response.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
  const history = () => page.evaluate(() => JSON.parse(sessionStorage.getItem(`kudo-assistant-v1:${document.getElementById('lab-assistant').dataset.employee}`)));
  const streaming = await openStream('Сравни эксперименты в потоке');
  event(streaming, 'status', {text:'Смотрю эксперименты'});
  await page.waitForFunction(() => document.getElementById('assistant-pending').textContent.includes('Смотрю эксперименты'));
  assert.equal(await page.locator('#assistant-cancel').isVisible(), true);
  assert.equal(await page.locator('.assistant-message.is-assistant').count(), 0, 'Activity updates do not invent an answer');
  const nextQuestionDraft = 'Черновик следующего вопроса';
  await page.locator('#assistant-input').fill(nextQuestionDraft);
  await page.locator('#assistant-input').press('Enter');
  assert.equal(await page.locator('#assistant-input').inputValue(), nextQuestionDraft, 'Enter while busy preserves the draft');
  assert.equal(captured.at(-1).messages.at(-1).content, 'Сравни эксперименты в потоке', 'Typing does not change the active request');
  event(streaming, 'status', {text:'Изучаю статистику'});
  await page.waitForFunction(() => document.getElementById('assistant-pending').textContent.includes('Изучаю статистику'));

  const preamble = '## Я проверю исходные данные\n\nСверю **эксперименты** с измерениями.';
  const firstPart = '## Явные результаты\n\nСравнение **опытов**';
  const bytes = Buffer.from(`event: delta\r\ndata: ${JSON.stringify({text:preamble})}\r\n\r\n`);
  const split = bytes.indexOf(Buffer.from('Я')) + 1;
  streaming.write(bytes.subarray(0, split));
  streaming.write(bytes.subarray(split, bytes.length - 1));
  streaming.write(bytes.subarray(bytes.length - 1));
  await page.waitForFunction(() => document.querySelector('.assistant-message.is-assistant')?.textContent.includes('Я проверю исходные данные'));
  assert.equal(await page.locator('#assistant-cancel').isVisible(), true, 'Text appears while the response is still open');
  assert.equal((await history()).filter(item => item.role === 'assistant').length, 0, 'Partial answers stay outside saved history');
  assert.equal((await page.locator('.assistant-message.is-assistant').textContent()).includes('\uFFFD'), false, 'UTF-8 split across chunks decodes cleanly');
  await page.screenshot({path:join(artifacts,'desktop-streaming.png'),fullPage:true});

  event(streaming, 'status', {text:'Смотрю испытание A',reset:true});
  await page.waitForFunction(() => document.getElementById('assistant-activity-text').textContent.includes('Смотрю испытание A'));
  assert.match(await page.locator('.assistant-message.is-assistant').textContent(), /Я проверю исходные данные/, 'A tool round preserves its public preamble');
  assert.equal((await history()).filter(item => item.role === 'assistant').length, 0, 'An unfinished turn stays outside the sent conversation history');
  const nextPreamble = 'Изучил опыт A. Теперь проверю **статистику**.';
  event(streaming, 'delta', {text:nextPreamble});
  await page.waitForFunction(() => document.querySelector('.assistant-message.is-assistant')?.textContent.includes('Теперь проверю'));
  event(streaming, 'status', {text:'Изучаю статистику опыта A',reset:true});
  await page.waitForFunction(() => document.getElementById('assistant-activity-text').textContent.includes('Изучаю статистику опыта A'));
  assert.match(await page.locator('.assistant-message.is-assistant').textContent(), /Я проверю исходные данные/);
  assert.match(await page.locator('.assistant-message.is-assistant').textContent(), /Теперь проверю статистику/);
  event(streaming, 'delta', {text:firstPart});
  await page.waitForFunction(() => document.querySelector('.assistant-message.is-assistant')?.textContent.includes('Явные результаты'));

  const secondPart = '\n\n| Опыт | Плотность |\n| --- | ---: |\n| A | 31 |';
  // SSE permits multiple data lines in one event. Their joined JSON remains
  // valid, and the resulting answer still uses the normal safe Markdown path.
  streaming.write(`event: delta\ndata: {"text":\ndata: ${JSON.stringify(secondPart)}}\n\n`);
  await page.waitForFunction(() => document.querySelector('.assistant-message.is-assistant')?.textContent.includes('Плотность'));
  event(streaming, 'done', {reply:firstPart + secondPart, sources:[{title:'Потоковый опыт A',url:'/tests/1/'}],model:'deepseek-test'});
  streaming.end();
  await page.locator('#assistant-pending').waitFor({state:'hidden'});
  assert.equal(await page.locator('.assistant-message.is-assistant h4').last().textContent(), 'Явные результаты');
  assert.equal(await page.locator('.assistant-message.is-assistant strong').last().textContent(), 'опытов');
  assert.equal(await page.locator('.assistant-table-scroll tbody td').last().textContent(), '31');
  assert.equal(await page.locator('.assistant-sources a[href="/tests/1/"]').textContent(), 'Потоковый опыт A');
  assert.equal((await history()).at(-1).content, firstPart + secondPart);
  assert.equal(await page.locator('#assistant-input').inputValue(), nextQuestionDraft, 'Completion preserves the next question draft');
  assert.deepEqual((await history()).at(-1).progress, [preamble,nextPreamble], 'Completed turns keep public stage text separately from the final answer');
  await page.reload({waitUntil:'networkidle'});
  assert.match(await page.locator('.assistant-message.is-assistant').textContent(), /Я проверю исходные данные/);
  assert.match(await page.locator('.assistant-message.is-assistant').textContent(), /Теперь проверю статистику/);
  assert.match(await page.locator('.assistant-message.is-assistant').textContent(), /Явные результаты/);
  await page.locator('#lab-assistant').evaluate((node, url) => { node.dataset.chatUrl = url; }, streamUrl);

  const cancelled = await openStream('Покажи ещё данные');
  assert.deepEqual(Object.keys(captured.at(-1).messages[1]).sort(), ['content','role'], 'Public stage annotations are not sent back as provider history');
  assert.equal(captured.at(-1).messages[1].content, firstPart + secondPart);
  event(cancelled, 'delta', {text:'Частичный ответ перед остановкой'});
  await page.waitForFunction(() => document.querySelector('.assistant-message.is-assistant:last-child')?.textContent.includes('Частичный ответ'));
  await page.locator('#assistant-input').fill(nextQuestionDraft);
  await page.locator('#assistant-input').press('Escape');
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.equal(await page.locator('#lab-assistant').isVisible(), true, 'Escape cancels the active response without closing the chat');
  assert.equal(await page.locator('#assistant-input').inputValue(), nextQuestionDraft, 'Cancellation preserves the next question draft');
  assert.match(await page.locator('#assistant-error-text').textContent(), /остановлен/);
  assert.match(await page.locator('.assistant-message.is-assistant').last().textContent(), /Ответ прерван/);
  assert.equal((await history()).some(item => item.content.includes('Частичный ответ')), false, 'Cancellation does not save an unfinished model turn');
  cancelled.end();
  const retried = await openStream('', true);
  assert.deepEqual(captured.at(-1).messages.map(item => item.role), ['user','assistant','user']);
  assert.equal(captured.at(-1).messages.some(item => item.content.includes('Частичный ответ')), false);
  event(retried, 'delta', {text:'Полный ответ после повтора'});
  event(retried, 'done', {reply:'Полный ответ после повтора',sources:[]});
  retried.end();
  await page.locator('#assistant-pending').waitFor({state:'hidden'});
  assert.equal(await page.locator('.assistant-message.is-assistant').count(), 2, 'Retry replaces the interrupted draft');
  assert.equal((await history()).at(-1).content, 'Полный ответ после повтора');

  const interrupted = await openStream('Проверка обрыва соединения');
  event(interrupted, 'delta', {text:'Ответ перед обрывом'});
  interrupted.end();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.equal(await page.locator('#assistant-cancel').isVisible(), false);
  assert.match(await page.locator('.assistant-message.is-assistant').last().textContent(), /Ответ прерван/);
  assert.equal((await history()).some(item => item.content.includes('Ответ перед обрывом')), false, 'EOF without done does not complete a turn');

  const failed = await openStream('Проверка ошибки в потоке');
  event(failed, 'status', {text:'Думаю'});
  event(failed, 'error', {error:'Cloud.ru прервал поток.',code:'provider_error'});
  failed.end();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.match(await page.locator('#assistant-error-text').textContent(), /Cloud.ru прервал поток/);

  const discarded = await openStream('Вопрос перед новым диалогом');
  event(discarded, 'delta', {text:'Эту черновую часть нужно убрать'});
  await page.waitForFunction(() => document.querySelector('.assistant-message.is-assistant:last-child')?.textContent.includes('черновую часть'));
  await page.locator('#assistant-new').click();
  event(discarded, 'done', {reply:'Поздний ответ старого диалога',sources:[]});
  discarded.end();
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(await page.locator('#assistant-welcome').isVisible(), true);
  assert.deepEqual(await history(), []);

  // Prompt recall is independent of the shorter conversation context, remains
  // local to this employee, and only takes over arrows in an empty composer.
  const prompts = () => page.evaluate(() => JSON.parse(localStorage.getItem(`kudo-assistant-v1:${document.getElementById('lab-assistant').dataset.employee}:prompts`) || '[]'));
  await page.evaluate(() => localStorage.setItem(`kudo-assistant-v1:${document.getElementById('lab-assistant').dataset.employee}:prompts`, '[]'));
  await page.reload({waitUntil:'networkidle'});
  mode = 'history';
  const composer = page.locator('#assistant-input');
  const questions = Array.from({length:27}, (_, index) => `Запрос из истории № ${index + 1}`);
  for (const question of questions) {
    await composer.fill(question);
    await page.locator('#assistant-send').click();
    await page.locator('#assistant-pending').waitFor({state:'hidden'});
  }
  assert.deepEqual(await prompts(), questions.slice(-25), 'Only the latest 25 submitted questions remain in local prompt history');
  assert.match(await page.locator('#assistant-input-hint').textContent(), /↑.*↓/);
  assert.match(await page.locator('#assistant-input-hint').textContent(), /пуст|истори/i);
  await page.locator('#assistant-new').click();
  assert.deepEqual(await prompts(), questions.slice(-25), 'A new conversation keeps prompt recall available');
  await composer.press('ArrowDown');
  assert.equal(await composer.inputValue(), '');
  await composer.press('ArrowUp');
  assert.equal(await composer.inputValue(), questions.at(-1));
  await composer.press('ArrowUp');
  assert.equal(await composer.inputValue(), questions.at(-2));
  await composer.press('ArrowDown');
  assert.equal(await composer.inputValue(), questions.at(-1));
  await composer.press('ArrowDown');
  assert.equal(await composer.inputValue(), '', 'Down past the newest question restores the empty composer');
  for (let index = 0; index < 30; index++) await composer.press('ArrowUp');
  assert.equal(await composer.inputValue(), questions[2], 'Up stops at the oldest retained question');
  for (let index = 0; index < 30; index++) await composer.press('ArrowDown');
  assert.equal(await composer.inputValue(), '');

  const untouchedComposition = await composer.evaluate(node => {
    const event = new KeyboardEvent('keydown', {key:'ArrowUp',bubbles:true,cancelable:true,isComposing:true});
    node.dispatchEvent(event);
    return {value:node.value,prevented:event.defaultPrevented};
  });
  assert.deepEqual(untouchedComposition, {value:'',prevented:false}, 'IME navigation does not start prompt recall');
  for (const modifier of ['ctrlKey','altKey','metaKey','shiftKey']) {
    assert.deepEqual(await composer.evaluate((node, modifier) => {
      const event = new KeyboardEvent('keydown', {key:'ArrowUp',bubbles:true,cancelable:true,[modifier]:true});
      node.dispatchEvent(event);
      return {value:node.value,prevented:event.defaultPrevented};
    }, modifier), {value:'',prevented:false}, `${modifier} keeps its normal arrow behavior`);
  }
  await composer.fill('Первая строка\nВторая строка');
  await composer.evaluate(node => node.setSelectionRange(node.value.length, node.value.length));
  await composer.press('ArrowUp');
  assert.equal(await composer.inputValue(), 'Первая строка\nВторая строка');
  assert.ok(await composer.evaluate(node => node.selectionStart < node.value.length), 'Nonempty multiline arrows move the caret');
  await composer.press('ArrowDown');
  assert.equal(await composer.inputValue(), 'Первая строка\nВторая строка');
  await composer.fill('');
  await composer.press('ArrowUp');
  await composer.press('End');
  await composer.pressSequentially(' исправленный');
  const editedQuestion = await composer.inputValue();
  await composer.press('ArrowUp');
  assert.equal(await composer.inputValue(), editedQuestion, 'Editing a recalled question exits history navigation');
  await composer.fill('');
  await page.reload({waitUntil:'networkidle'});
  await composer.press('ArrowUp');
  assert.equal(await composer.inputValue(), questions.at(-1), 'Prompt recall survives page reload');
  assert.deepEqual(await prompts(), questions.slice(-25));

  mode = 'error';
  await composer.fill('Запрос с повтором');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  const beforeRetry = await prompts();
  assert.equal(beforeRetry.length, 25);
  assert.equal(beforeRetry.at(-1), 'Запрос с повтором', 'Failed submitted questions are available for recall');
  mode = 'history';
  await page.locator('#assistant-retry').click();
  await page.locator('#assistant-pending').waitFor({state:'hidden'});
  assert.deepEqual(await prompts(), beforeRetry, 'Retry does not duplicate the submitted question');
  assert.deepEqual(errors, []);
  console.log('Assistant browser checks passed: progressive SSE statuses/text, UTF-8 and event boundaries, preserved public stage text and completion, sources, stream errors/EOF, cancellation and retry, dialog reset, desktop/tablet/mobile, Markdown tables and safe rendering, pointer/keyboard resizing, unsaved form context and limits, fresh retry snapshots, width and conversation persistence, latest-25 local prompt recall and caret/IME behavior, keyboard/modal access and configuration.');
  console.log(`Screenshots: ${artifacts}`);
} finally {
  await browser?.close();
  for (const socket of streamSockets) socket.destroy();
  if (streamServer) await new Promise(resolve => streamServer.close(resolve));
  if (server) {
    server.kill('SIGTERM');
    const timeout = setTimeout(() => server.kill('SIGKILL'), 3000);
    await stopped; clearTimeout(timeout);
  }
  rmSync(scratch, {recursive:true,force:true});
}
