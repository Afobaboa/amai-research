import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.KUDO_PLAYWRIGHT_MODULE || 'playwright');

// The durable conversation API is mocked at the browser boundary. This check never calls a model
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
  let configured = true, mode = 'success', captured = [];
  let counter = 0;
  const chats = new Map();
  const reply = [
    '## Подтверждённые данные',
    '',
    'Смотрите **рецептуру** [DEMO](/recipes/1/).',
    '',
    'Создать: [Новое испытание](/tests/new/?recipe=8).',
    'Старый ответ: `/tests/new/`, /tests/ и **`/tests/1/results/new/`**.',
    'Шаблон: `/tests/{id}/results/new/`, [Карточка](/tests/{id}/).',
    'Подпись адресом: [/tests/new/](/tests/new/). Плотность 1 г/мл.',
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

  await context.route('**/assistant/status/', route => route.fulfill({json:{configured,model:'deepseek-test'}}));
  await context.route('**/assistant/conversations/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const parts = path.split('/').filter(Boolean).slice(2);
    const data = request.method() === 'POST' ? request.postDataJSON() : null;
    if (request.method() === 'POST') assert.ok(request.headers()['x-csrftoken']);
    if (!parts.length) {
      if (request.method() === 'POST') {
        const chat = {id:++counter, title:'Новый диалог', jobs:[], active:false};
        chats.set(chat.id, chat);
        return route.fulfill({status:201,json:chat});
      }
      return route.fulfill({json:{conversations:[...chats.values()].reverse(),next_before:null}});
    }
    const chat = chats.get(Number(parts[0]));
    if (!chat) return route.fulfill({status:404,json:{error:'Не найден'}});
    if (parts[1] === 'send') {
      captured.push(data);
      if (mode === 'error') return route.fulfill({status:503,json:{error:'Cloud.ru временно недоступен.'}});
      const job = {id:data.request_id, question:data.question, status:mode === 'pending' ? 'running':'completed',
        reply:mode === 'pending' ? '' : reply, progress:[], activity:'Смотрю эксперименты…',
        sources:[{title:'DEMO-316',url:'/recipes/1/'},{title:'Опасная ссылка',url:'javascript:alert(1)'}]};
      chat.jobs.push(job); chat.title = chat.jobs[0].question; chat.active = mode === 'pending';
      return route.fulfill({status:202,json:{id:job.id}});
    }
    if (parts[1] === 'jobs') {
      const job = chat.jobs.find(job => job.id === parts[2]);
      job.status = 'cancelled'; chat.active = false;
      return route.fulfill({json:{status:'cancelled'}});
    }
    return route.fulfill({json:{...chat,next_before:null}});
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
  assert.ok(captured[0].question);
  assert.equal(Object.hasOwn(captured[0], 'form_state'), false, 'Pages without a form do not attach a draft');
  assert.equal(await page.locator('.assistant-message img').count(), 0);
  assert.equal(await page.locator('.assistant-message a[href^="javascript:"]').count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.locator('.assistant-sources a[href="/recipes/1/"]').count(), 0, 'Sources do not repeat inline links');
  const firstReply = page.locator('.assistant-message.is-assistant').first();
  assert.equal(await firstReply.getByRole('link', {name:'Новое испытание', exact:true}).count(), 3);
  assert.equal(await firstReply.locator('a[href="/tests/new/?recipe=8"]').textContent(), 'Новое испытание');
  assert.equal(await firstReply.locator('a[href="/tests/"]').textContent(), 'Испытания');
  assert.equal(await firstReply.locator('strong a[href="/tests/1/results/new/"]').textContent(), 'Добавить результат');
  assert.equal(await firstReply.locator('a[href*="{id}"]').count(), 0);
  assert.equal(await firstReply.locator('a').filter({hasText:/^\//}).count(), 0, 'Route text is replaced with a UI label');
  assert.ok((await firstReply.textContent()).includes('Плотность 1 г/мл.'), 'Units remain intact');
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

  // Background polling must leave the native picker intact while it is open
  // and when it is dismissed without choosing another conversation.
  const picker = page.locator('#assistant-conversations');
  await page.waitForFunction(() => document.getElementById('assistant-conversations').options.length === 2);
  await page.evaluate(() => {
    const select = document.getElementById('assistant-conversations');
    const panel = document.getElementById('lab-assistant');
    window.pickerCheck = {options:[...select.options], mutations:0, moved:false,
      box:JSON.stringify(panel.getBoundingClientRect().toJSON()), running:true};
    window.pickerObserver = new MutationObserver(records => { window.pickerCheck.mutations += records.length; });
    window.pickerObserver.observe(select, {childList:true, subtree:true, characterData:true, attributes:true});
    const sample = () => {
      if (JSON.stringify(panel.getBoundingClientRect().toJSON()) !== window.pickerCheck.box) window.pickerCheck.moved = true;
      if (window.pickerCheck.running) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  const nextPickerPoll = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/assistant/conversations/' && response.request().method() === 'GET');
  await picker.click();
  await nextPickerPoll;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const headingBox = await page.locator('#assistant-title').boundingBox();
  await page.mouse.click(headingBox.x + 5, headingBox.y + 5);
  await page.locator('#assistant-input').focus();
  const pickerCheck = await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => {
    window.pickerCheck.running = false;
    window.pickerObserver.disconnect();
    resolve({mutations:window.pickerCheck.mutations, moved:window.pickerCheck.moved,
      sameOptions:window.pickerCheck.options.every((option, index) => option === document.getElementById('assistant-conversations').options[index])});
  })));
  assert.equal(pickerCheck.mutations, 0, 'An unchanged background refresh must not rebuild the chat picker');
  assert.equal(pickerCheck.sameOptions, true, 'Native option nodes must survive polling and dismissal');
  assert.equal(pickerCheck.moved, false, 'Opening and dismissing the picker must not move the assistant panel');

  // Real updates arrive during interaction, then appear after leaving the picker.
  await picker.focus();
  const selectedChat = chats.get(Number(await picker.inputValue()));
  const previousTitle = selectedChat.title;
  selectedChat.title = previousTitle + ' · обновлено';
  const changedPickerPoll = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/assistant/conversations/' && response.request().method() === 'GET');
  await changedPickerPoll;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await picker.locator('option:checked').textContent(), previousTitle, 'Keep the focused native menu stable when data changes');
  await page.locator('#assistant-input').focus();
  await page.waitForFunction(title => document.querySelector('#assistant-conversations option:checked')?.textContent === title, selectedChat.title);
  assert.equal(await page.evaluate(() => window.pickerCheck.options.every((option, index) => option === document.getElementById('assistant-conversations').options[index])), true, 'Changed labels update existing options after blur');

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

  await page.locator('.assistant-message-body a[href="/recipes/1/"]').first().click();
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

  mode = 'pending';
  await page.locator('#assistant-input').fill('Долгий вопрос');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  const firstChat = await page.locator('#assistant-conversations').inputValue();
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  assert.equal(captured.filter(item => item.question === 'Долгий вопрос').length, 1, 'Reload does not resubmit a running request');
  await page.locator('#assistant-new').click();
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(chats.get(Number(firstChat)).jobs.at(-1).status, 'running', 'New chat does not cancel the previous generation');
  const chatCount = chats.size;
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#assistant-conversations').inputValue(), '', 'An explicitly selected new chat survives reload');
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(await page.locator('#assistant-send').isEnabled(), true);
  await page.goto(base + '/recipes/1/', {waitUntil:'networkidle'});
  assert.equal(await page.locator('#assistant-conversations').inputValue(), '', 'New chat also survives page navigation');
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(chats.size, chatCount, 'Restoring an empty selection does not create a saved conversation');
  mode = 'success';
  await page.locator('#assistant-input').fill('Другая тема');
  await page.locator('#assistant-send').click();
  await page.locator('.assistant-message.is-assistant').waitFor();
  const secondChat = await page.locator('#assistant-conversations').inputValue();
  assert.notEqual(secondChat, firstChat);
  await page.locator('#assistant-conversations').selectOption(firstChat);
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  assert.equal(await page.locator('#assistant-conversations').inputValue(), firstChat, 'An older selected conversation survives reload');
  await page.locator('#assistant-cancel').click();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.match(await page.locator('#assistant-error-text').textContent(), /остановлен/);
  assert.equal(chats.get(Number(firstChat)).jobs.at(-1).status, 'cancelled');
  await page.locator('#assistant-conversations').selectOption('');
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#assistant-conversations').inputValue(), '', 'Selecting new chat from the picker survives reload');
  assert.equal(await page.locator('.assistant-message').count(), 0);

  // Simulate server completion after the browser tab has been closed.
  await page.locator('#assistant-new').click();
  mode = 'pending';
  await page.locator('#assistant-input').fill('Ответ после закрытия вкладки');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  const closedChat = await page.locator('#assistant-conversations').inputValue();
  const job = chats.get(Number(closedChat)).jobs.at(-1);
  await page.close();
  job.status = 'completed'; job.reply = reply; chats.get(Number(closedChat)).active = false;
  const returned = await context.newPage();
  returned.on('pageerror', error => errors.push(error.message));
  await returned.goto(base, {waitUntil:'networkidle'});
  await returned.locator('.assistant-message.is-assistant').waitFor();
  assert.equal(await returned.locator('#assistant-conversations').inputValue(), closedChat);
  assert.match(await returned.locator('.assistant-message.is-user').textContent(), /после закрытия/);
  await returned.locator('#assistant-conversations').selectOption(secondChat);
  await returned.waitForFunction(() => document.querySelector('.assistant-message.is-user')?.textContent.includes('Другая тема'));
  assert.equal(await returned.locator('.assistant-message.is-user').count(), 1, 'Chats have independent transcripts');
  await returned.setViewportSize({width:390,height:844});
  await returned.waitForTimeout(100);
  if (!await returned.locator('#lab-assistant').isVisible()) await returned.locator('#assistant-launcher').click();
  await returned.waitForFunction(() => document.getElementById('lab-assistant').getAttribute('aria-modal') === 'true');
  assert.equal(await returned.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const wide = returned.locator('.assistant-table-scroll').last();
  assert.ok(await wide.evaluate(node => node.scrollWidth > node.clientWidth));
  await returned.screenshot({path:join(artifacts,'mobile-tables.png')});
  await returned.close();
  const formPage = await context.newPage();
  formPage.on('pageerror', error => errors.push(error.message));
  await formPage.goto(base + '/recipes/new/', {waitUntil:'networkidle'});
  await formPage.locator('#assistant-new').click();
  await formPage.locator('#id_title').fill('Несохранённый черновик');
  await formPage.locator('#id_components-0-quantity_value').fill('0');
  await formPage.evaluate(() => {
    const node = document.createElement('input');
    node.type = 'password'; node.name = 'qa_password'; node.value = 'DO-NOT-SEND';
    document.getElementById('id_title').form.append(node);
  });
  mode = 'success';
  await formPage.locator('#assistant-input').fill('Проверь форму');
  await formPage.locator('#assistant-send').click();
  await formPage.locator('.assistant-message.is-assistant').waitFor();
  const draft = captured.at(-1);
  const fields = draft.form_state.forms.flatMap(form => form.fields);
  assert.equal(fields.find(field => field.name === 'title').value, 'Несохранённый черновик');
  assert.equal(fields.find(field => field.name === 'components-0-quantity_value').value, '0');
  assert.equal(JSON.stringify(draft).includes('DO-NOT-SEND'), false);
  assert.equal(fields.some(field => field.name === 'csrfmiddlewaretoken'), false);
  assert.equal(await formPage.locator('#id_title').inputValue(), 'Несохранённый черновик');
  await formPage.locator('#assistant-input').focus();
  await formPage.keyboard.press('ArrowUp');
  assert.equal(await formPage.locator('#assistant-input').inputValue(), 'Проверь форму');
  await formPage.keyboard.press('ArrowDown');
  assert.equal(await formPage.locator('#assistant-input').inputValue(), '');
  assert.deepEqual(errors, []);
  console.log('Assistant browser checks passed: saved chats, reload, tab closure, cancellation, rendering, resizing, mobile, and form context.');
} finally {
  await browser?.close();
  if (server && server.exitCode === null) { server.kill('SIGTERM'); await stopped; }
  rmSync(scratch, {recursive:true,force:true});
}
