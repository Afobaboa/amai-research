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
  for (const args of [['migrate','--noinput'], ['seed_initial']]) {
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
    'Смотрите **рецептуру** [316.1](/recipes/1/).',
    '',
    'Создать: [Новый эксперимент](/experiments/new/?recipe=8).',
    'Старый ответ: `/experiments/new/`, /experiments/ и **`/experiments/1/measurement-groups/new/`**.',
    'Шаблон: `/experiments/{id}/measurement-groups/new/`, [Карточка](/experiments/{id}/).',
    'Подпись адресом: [/experiments/new/](/experiments/new/). Плотность 1 г/мл.',
    '',
    '| **Рецептура** | Значение | Комментарий |',
    '| :--- | ---: | :---: |',
    String.raw`| [316.1](/recipes/1/) | 316 | A\|B |`,
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
        const chat = {id:++counter, title:'Новый диалог', jobs:[], active:false, updated_at:new Date().toISOString()};
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
        sources:[{title:'Зимняя улучшенная пена — 316.1',url:'/recipes/1/'},{title:'Опасная ссылка',url:'javascript:alert(1)'}]};
      chat.jobs.push(job); chat.title = chat.jobs[0].question; chat.active = mode === 'pending';
      chat.updated_at = new Date().toISOString();
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
  const employeeValue = await page.locator('#id_employee option').evaluateAll(nodes => nodes.find(node => node.textContent.includes('Гущин Даниил')).value);
  await page.locator('#id_employee').selectOption(employeeValue);
  await page.getByRole('button',{name:/Открыть лабораторию/}).click();
  await page.waitForLoadState('networkidle');
  await page.locator('#lab-assistant').waitFor({state:'visible'});
  assert.equal(await page.title(),'АМАИ РАЗРАБОТКА · Обзор лаборатории');
  assert.equal(await page.locator('.topbar-meta').count(), 0);
  assert.doesNotMatch(await page.locator('body').innerText(), /AMAI LAB/);
  assert.equal(await page.locator('#assistant-title').textContent(),'ИИ-ассистент');
  assert.equal(await page.locator('.assistant-welcome-mark').count(), 0);
  assert.equal(await page.locator('#assistant-model,.assistant-context,.assistant-disclosure,.workspace-label').count(),0);
  assert.doesNotMatch(await page.locator('body').innerText(),/deepseek-test|KUDO|Помощник лаборатории/);
  for (const selector of ['#assistant-new','#assistant-send']) {
    assert.equal(await page.locator(`${selector} svg[aria-hidden="true"]`).count(),1);
  }
  assert.match(await page.locator('link[rel="icon"]').getAttribute('href'),/favicon\.svg\?v=amai-3$/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  for (const selector of ['.assistant-header','#assistant-form']) {
    const box = await page.locator(selector).boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height <= 1000, `${selector} must remain in the viewport`);
  }
  const input = page.locator('#assistant-input');
  const hint = page.locator('#assistant-input-hint');
  const inputHelp = page.getByRole('button', {name:'Подсказки по вводу'});
  const emptyInputHeight = (await input.boundingBox()).height;
  assert.ok((await page.locator('#assistant-form').boundingBox()).height < 90,
    'An empty composer leaves room for the conversation');
  assert.equal(await hint.isVisible(), false);
  await inputHelp.hover();
  await hint.waitFor({state:'visible'});
  await page.mouse.move(0, 0);
  await hint.waitFor({state:'hidden'});
  await inputHelp.focus();
  await hint.waitFor({state:'visible'});
  await page.keyboard.press('Escape');
  assert.equal(await hint.isVisible(), false);
  assert.equal(await page.locator('#lab-assistant').isVisible(), true,
    'Escape dismisses the hint before closing the assistant');
  await inputHelp.click();
  await hint.waitFor({state:'visible'});
  await inputHelp.click();
  await hint.waitFor({state:'hidden'});
  await input.fill('Первая строка');
  await input.press('Shift+Enter');
  assert.equal(await input.inputValue(), 'Первая строка\n');
  assert.equal(captured.length, 0, 'A newline does not submit a question');
  await input.fill(Array(12).fill('Длинный вопрос об исследовании').join('\n'));
  assert.ok((await input.boundingBox()).height > emptyInputHeight);
  assert.equal(await input.evaluate(node => node.scrollHeight > node.clientHeight), true,
    'A long draft scrolls inside the composer');
  await input.fill('');
  assert.equal((await input.boundingBox()).height, emptyInputHeight);
  await page.screenshot({path:join(artifacts,'desktop.png'),fullPage:true});
  // Reproduce HTTP deployments where randomUUID is unavailable.
  await page.evaluate(() => Object.defineProperty(crypto, 'randomUUID', {
    value:undefined, configurable:true,
  }));
  await page.locator('[data-assistant-prompt]').first().click();
  await page.locator('.assistant-message.is-assistant').waitFor();
  assert.match(captured[0].request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    'Sending without randomUUID still supplies a valid UUID v4');
  await page.evaluate(() => { delete crypto.randomUUID; });
  assert.equal(captured[0].page, '/');
  assert.ok(captured[0].question);
  assert.equal(Object.hasOwn(captured[0], 'form_state'), false, 'Pages without a form do not attach a draft');
  assert.equal(await page.locator('.assistant-message img').count(), 0);
  assert.equal(await page.locator('.assistant-message a[href^="javascript:"]').count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.locator('.assistant-sources a[href="/recipes/1/"]').count(), 0, 'Sources do not repeat inline links');
  const firstReply = page.locator('.assistant-message.is-assistant').first();
  assert.equal(await firstReply.getByRole('link', {name:'Новый эксперимент', exact:true}).count(), 3);
  assert.equal(await firstReply.locator('a[href="/experiments/new/?recipe=8"]').textContent(), 'Новый эксперимент');
  assert.equal(await firstReply.locator('a[href="/experiments/"]').textContent(), 'Эксперименты');
  assert.equal(await firstReply.locator('strong a[href="/experiments/1/measurement-groups/new/"]').textContent(), 'Добавить группу измерений');
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

  const selectedChatId = target => target.locator('#assistant-conversations').getAttribute('data-chat-id');
  const chooseSavedChat = async (target, id) => {
    await target.locator('#assistant-conversations').click();
    await target.locator(id ? `#assistant-chat-list button[data-chat-id="${id}"]` : '#assistant-chat-new').click();
    await target.locator('#assistant-chat-menu').waitFor({state:'hidden'});
  };

  // Include older, long, and undated titles in the history without involving a model.
  for (const [title, updated_at] of [
    ['Сравнение зимних рецептур', new Date(Date.now() - 86400000).toISOString()],
    ['Проверка гипотезы о влиянии нового сырья на стабильность пены при низкой температуре · QA-302_А.Б', new Date(Date.now() - 8 * 86400000).toISOString()],
    ['Результаты испытаний', undefined],
  ]) {
    const chat = {id:++counter, title, updated_at, jobs:[], active:false};
    chats.set(chat.id, chat);
  }

  // Background polling must preserve the open history, focus, and its keyed rows.
  const picker = page.locator('#assistant-conversations');
  const history = page.locator('#assistant-chat-menu');
  const search = page.locator('#assistant-chat-search');
  const historyRows = page.locator('#assistant-chat-list button[data-chat-id]');
  await page.waitForFunction(() => document.querySelectorAll('#assistant-chat-list button[data-chat-id]').length === 4);
  assert.equal(await picker.evaluate(node => node.tagName), 'BUTTON');
  assert.equal(await picker.getAttribute('aria-haspopup'), 'dialog');
  await picker.click();
  await history.waitFor({state:'visible'});
  assert.equal(await history.getAttribute('role'), 'dialog');
  assert.equal(await search.evaluate(node => node === document.activeElement), true);
  assert.equal(await page.locator('#assistant-chat-list button[aria-current="true"]').getAttribute('data-chat-id'), await selectedChatId(page));
  await search.press('ArrowDown');
  assert.equal(await historyRows.first().evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('End');
  assert.equal(await historyRows.last().evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Home');
  assert.equal(await historyRows.first().evaluate(node => node === document.activeElement), true);
  await page.evaluate(() => {
    const list = document.getElementById('assistant-chat-list');
    const panel = document.getElementById('lab-assistant');
    window.pickerCheck = {rows:[...list.querySelectorAll('button[data-chat-id]')], mutations:0, moved:false,
      box:JSON.stringify(panel.getBoundingClientRect().toJSON()), running:true};
    window.pickerObserver = new MutationObserver(records => { window.pickerCheck.mutations += records.length; });
    window.pickerObserver.observe(list, {childList:true, subtree:true, characterData:true, attributes:true});
    const sample = () => {
      if (JSON.stringify(panel.getBoundingClientRect().toJSON()) !== window.pickerCheck.box) window.pickerCheck.moved = true;
      if (window.pickerCheck.running) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  const nextPickerPoll = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/assistant/conversations/' && response.request().method() === 'GET');
  await nextPickerPoll;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await historyRows.first().evaluate(node => node === document.activeElement), true, 'Polling preserves the focused history row');
  const headingBox = await page.locator('#assistant-title').boundingBox();
  await page.mouse.click(headingBox.x + 5, headingBox.y + 5);
  await history.waitFor({state:'hidden'});
  await page.locator('#assistant-input').focus();
  const pickerCheck = await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => {
    window.pickerCheck.running = false;
    window.pickerObserver.disconnect();
    resolve({mutations:window.pickerCheck.mutations, moved:window.pickerCheck.moved,
      sameRows:window.pickerCheck.rows.every(row => document.getElementById('assistant-chat-list').contains(row))});
  })));
  assert.equal(pickerCheck.mutations, 0, 'An unchanged background refresh must not rebuild the chat picker');
  assert.equal(pickerCheck.sameRows, true, 'History row nodes must survive polling and dismissal');
  assert.equal(pickerCheck.moved, false, 'Opening and dismissing the picker must not move the assistant panel');

  // Real updates arrive during interaction, then appear after leaving the picker.
  await picker.click();
  const selectedChat = chats.get(Number(await selectedChatId(page)));
  const selectedTitle = page.locator(`#assistant-chat-list button[data-chat-id="${selectedChat.id}"] .assistant-chat-title`);
  const previousTitle = selectedChat.title;
  selectedChat.title = previousTitle + ' · обновлено';
  const changedPickerPoll = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/assistant/conversations/' && response.request().method() === 'GET');
  await changedPickerPoll;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await selectedTitle.textContent(), previousTitle, 'Keep the open history stable when background data changes');
  await page.locator('#assistant-input').focus();
  await history.waitFor({state:'hidden'});
  await page.waitForFunction(title => document.getElementById('assistant-current-chat')?.textContent === title, selectedChat.title);
  assert.equal(await page.evaluate(() => window.pickerCheck.rows.every(row => document.getElementById('assistant-chat-list').contains(row))), true, 'Changed titles update existing rows after dismissal');

  await picker.click();
  await search.fill('ЗИМНИХ');
  assert.equal(await historyRows.count(), 1, 'History search ignores title case');
  assert.equal(await historyRows.first().locator('.assistant-chat-title').textContent(), 'Сравнение зимних рецептур');
  await search.fill('СРАВНЕНИЕ_зимних.рецептур');
  assert.equal(await historyRows.count(), 1,
    'History search treats spaces and punctuation as equivalent');
  for (const query of [
    'A_302-А_Б', 'a.302.а.б', '  a-302_а.б  ', 'A 302 А Б',
    'A - 302__А..Б', 'a\u00a0302\u00a0а\u00a0б',
  ]) {
    await search.fill(query);
    assert.equal(await historyRows.count(), 1,
      'History search matches substrings with equivalent separators and case');
    assert.match(await historyRows.first().locator('.assistant-chat-title')
      .textContent(), /QA-302_А\.Б$/);
  }
  await search.fill('нет-такого-диалога-123');
  assert.equal(await historyRows.count(), 0);
  assert.equal(await page.locator('#assistant-chat-empty').isVisible(), true);
  assert.equal(await page.locator('#assistant-chat-new').isVisible(), true, 'A new chat remains available with no search results');
  await search.fill('');
  const historyBox = await history.boundingBox(), assistantBox = await page.locator('#lab-assistant').boundingBox();
  assert.ok(historyBox.x >= assistantBox.x && historyBox.x + historyBox.width <= assistantBox.x + assistantBox.width + 1, 'History fits within the desktop panel');
  assert.ok(historyBox.y >= 0 && historyBox.y + historyBox.height <= 1000, 'History fits within the desktop viewport');
  await page.screenshot({path:join(artifacts,'desktop-history.png'),fullPage:true});
  await search.focus();
  await page.keyboard.press('Escape');
  await history.waitFor({state:'hidden'});
  try {
    await page.waitForFunction(() => document.activeElement?.id ===
      'assistant-conversations');
  } catch (error) {
    console.error('History Escape focus:', await page.evaluate(() => ({
      active: document.activeElement?.outerHTML,
      trigger: document.getElementById('assistant-conversations')?.outerHTML,
      historyHidden: document.getElementById('assistant-chat-menu')?.hidden,
      panelHidden: document.getElementById('lab-assistant')?.hidden,
    })));
    throw error;
  }
  assert.equal(await picker.evaluate(node => node === document.activeElement), true, 'Escape restores history trigger focus');
  assert.equal(await page.locator('#lab-assistant').isVisible(), true);

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
  for (const path of ['/recipes/','/experiments/','/recipes/new/','/recipes/1/']) {
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
  const firstChat = await selectedChatId(page);
  await page.locator('#assistant-conversations').click();
  await page.locator('#assistant-chat-search').press('Escape');
  assert.equal(await page.locator('#assistant-chat-menu').isVisible(), false);
  assert.equal(chats.get(Number(firstChat)).jobs.at(-1).status, 'running', 'Escape dismisses history without cancelling a running reply');
  assert.equal(await page.locator('#assistant-cancel').isVisible(), true);
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  assert.equal(captured.filter(item => item.question === 'Долгий вопрос').length, 1, 'Reload does not resubmit a running request');
  await page.locator('#assistant-new').click();
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(chats.get(Number(firstChat)).jobs.at(-1).status, 'running', 'New chat does not cancel the previous generation');
  const chatCount = chats.size;
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await selectedChatId(page), '', 'An explicitly selected new chat survives reload');
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(await page.locator('#assistant-send').isEnabled(), true);
  await page.goto(base + '/recipes/1/', {waitUntil:'networkidle'});
  assert.equal(await selectedChatId(page), '', 'New chat also survives page navigation');
  assert.equal(await page.locator('.assistant-message').count(), 0);
  assert.equal(chats.size, chatCount, 'Restoring an empty selection does not create a saved conversation');
  mode = 'success';
  await page.locator('#assistant-input').fill('Другая тема');
  await page.locator('#assistant-send').click();
  await page.locator('.assistant-message.is-assistant').waitFor();
  const secondChat = await selectedChatId(page);
  assert.notEqual(secondChat, firstChat);
  await chooseSavedChat(page, firstChat);
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  assert.equal(await selectedChatId(page), firstChat, 'An older selected conversation survives reload');
  await page.locator('#assistant-cancel').click();
  await page.locator('#assistant-error').waitFor({state:'visible'});
  assert.match(await page.locator('#assistant-error-text').textContent(), /остановлен/);
  assert.equal(chats.get(Number(firstChat)).jobs.at(-1).status, 'cancelled');
  await chooseSavedChat(page, '');
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await selectedChatId(page), '', 'Selecting new chat from the picker survives reload');
  assert.equal(await page.locator('.assistant-message').count(), 0);

  // Simulate server completion after the browser tab has been closed.
  await page.locator('#assistant-new').click();
  mode = 'pending';
  await page.locator('#assistant-input').fill('Ответ после закрытия вкладки');
  await page.locator('#assistant-send').click();
  await page.locator('#assistant-cancel').waitFor({state:'visible'});
  const closedChat = await selectedChatId(page);
  const job = chats.get(Number(closedChat)).jobs.at(-1);
  await page.close();
  job.status = 'completed'; job.reply = reply; chats.get(Number(closedChat)).active = false;
  const returned = await context.newPage();
  returned.on('pageerror', error => errors.push(error.message));
  await returned.goto(base, {waitUntil:'networkidle'});
  await returned.locator('.assistant-message.is-assistant').waitFor();
  assert.equal(await selectedChatId(returned), closedChat);
  assert.match(await returned.locator('.assistant-message.is-user').textContent(), /после закрытия/);
  await chooseSavedChat(returned, secondChat);
  await returned.waitForFunction(() => document.querySelector('.assistant-message.is-user')?.textContent.includes('Другая тема'));
  assert.equal(await returned.locator('.assistant-message.is-user').count(), 1, 'Chats have independent transcripts');
  await returned.setViewportSize({width:390,height:844});
  await returned.waitForTimeout(100);
  if (!await returned.locator('#lab-assistant').isVisible()) await returned.locator('#assistant-launcher').click();
  await returned.waitForFunction(() => document.getElementById('lab-assistant').getAttribute('aria-modal') === 'true');
  await returned.locator('#assistant-conversations').click();
  await returned.locator('#assistant-chat-menu').waitFor({state:'visible'});
  const mobileHistory = await returned.locator('#assistant-chat-menu').boundingBox();
  assert.ok(mobileHistory.x >= 0 && mobileHistory.x + mobileHistory.width <= 390, 'History fits the mobile width');
  assert.ok(mobileHistory.y >= 0 && mobileHistory.y + mobileHistory.height <= 844, 'History fits the mobile height');
  assert.equal(await returned.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Long history titles do not overflow on mobile');
  await returned.screenshot({path:join(artifacts,'mobile-history.png')});
  await returned.locator('#assistant-chat-search').press('Escape');
  assert.equal(await returned.locator('#lab-assistant').isVisible(), true, 'Escape dismisses mobile history before closing the assistant');
  await returned.locator('#assistant-input-help').click();
  await returned.locator('#assistant-input-hint').waitFor({state:'visible'});
  const hintBox = await returned.locator('#assistant-input-hint').boundingBox();
  assert.ok(hintBox.x >= 0 && hintBox.x + hintBox.width <= 390,
    'The input hint fits on mobile');
  await returned.locator('#assistant-input').click();
  assert.equal(await returned.locator('#assistant-input-hint').isVisible(), false);
  assert.equal(await returned.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const wide = returned.locator('.assistant-table-scroll').last();
  assert.ok(await wide.evaluate(node => node.scrollWidth > node.clientWidth));
  await returned.screenshot({path:join(artifacts,'mobile-tables.png')});
  await returned.close();
  const formPage = await context.newPage();
  formPage.on('pageerror', error => errors.push(error.message));
  await formPage.goto(base + '/recipes/new/', {waitUntil:'networkidle'});
  assert.equal(await formPage.locator('#lab-assistant').isVisible(), false,
    'A fresh recipe tab starts with the assistant collapsed');
  await formPage.locator('#assistant-launcher').click();
  await formPage.locator('#assistant-new').click();
  await formPage.locator('#id_title').fill('Несохранённый черновик');
  await formPage.locator('#id_polyol-0-quantity').fill('0');
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
  assert.equal(fields.find(field => field.name === 'polyol-0-quantity').value, '0');
  assert.equal(JSON.stringify(draft).includes('DO-NOT-SEND'), false);
  assert.equal(fields.some(field => field.name === 'csrfmiddlewaretoken'), false);
  assert.equal(await formPage.locator('#id_title').inputValue(), 'Несохранённый черновик');
  await formPage.locator('#assistant-input').focus();
  await formPage.keyboard.press('ArrowUp');
  assert.equal(await formPage.locator('#assistant-input').inputValue(), 'Проверь форму');
  await formPage.keyboard.press('ArrowDown');
  assert.equal(await formPage.locator('#assistant-input').inputValue(), '');

  // A separate page exercises pagination without changing the durable-chat scenarios above.
  const paginated = await context.newPage();
  paginated.on('pageerror', error => errors.push(error.message));
  const historyChats = [70003, 70002, 70001].map(id => ({
    id, title:`История исследования ${id}`, active:false,
    updated_at:new Date(Date.now() - (70003 - id) * 86400000).toISOString(),
  }));
  let moreRequests = 0, releaseHistoryPage;
  await paginated.route('**/assistant/conversations/**', async route => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split('/').filter(Boolean).slice(2);
    if (parts.length) {
      const chat = historyChats.find(chat => chat.id === Number(parts[0])) || historyChats[0];
      return route.fulfill({json:{...chat, jobs:[], next_before:null}});
    }
    if (url.searchParams.has('before')) {
      assert.equal(url.searchParams.get('before'), '70002');
      moreRequests++;
      await new Promise(resolve => { releaseHistoryPage = resolve; });
      return route.fulfill({json:{conversations:historyChats.slice(2), next_before:null}});
    }
    return route.fulfill({json:{conversations:historyChats.slice(0, 2), next_before:70002}});
  });
  await paginated.goto(base, {waitUntil:'networkidle'});
  await paginated.locator('#assistant-conversations').click();
  const paginatedSearch = paginated.locator('#assistant-chat-search');
  const moreChats = paginated.locator('#assistant-more-chats');
  const checkSearchStyle = async width => {
    const style = await paginatedSearch.evaluate(node => {
      const style = getComputedStyle(node);
      return {padding:style.padding, border:style.borderWidth, height:style.height, fontSize:style.fontSize};
    });
    assert.deepEqual(style, {padding:'0px', border:'0px', height:'22px', fontSize:width < 640 ? '16px' : '13px'},
      'History search uses one surrounding field frame and a compact inner input');
  };
  await checkSearchStyle(1440);
  await paginatedSearch.fill('История');
  assert.equal(await paginated.locator('#assistant-chat-search-note').isVisible(), true,
    'Search explains when only loaded conversations are included');
  assert.equal(await paginated.locator('#assistant-chat-list button[data-chat-id]').count(), 2);
  const moreHistoryRequest = paginated.waitForRequest(request =>
    new URL(request.url()).searchParams.get('before') === '70002');
  await moreChats.click();
  await moreHistoryRequest;
  await paginated.waitForFunction(() => document.getElementById('assistant-more-chats').getAttribute('aria-disabled') === 'true');
  assert.equal(await moreChats.evaluate(node => node === document.activeElement), true,
    'Loading another page preserves keyboard focus');
  await paginated.keyboard.press('Enter');
  assert.equal(moreRequests, 1, 'Repeated activation cannot enqueue duplicate history pages');
  assert.equal(typeof releaseHistoryPage, 'function');
  releaseHistoryPage();
  await moreChats.waitFor({state:'hidden'});
  assert.equal(await paginated.locator('#assistant-chat-list button[data-chat-id="70001"]').evaluate(node => node === document.activeElement), true,
    'The first appended conversation receives focus when the final page removes the load-more button');
  assert.equal(await paginated.locator('#assistant-chat-list button[data-chat-id]').count(), 3);
  assert.equal(await paginated.locator('#assistant-chat-search-note').isVisible(), false);
  for (const [width, height, filename] of [
    [1440, 400, 'desktop-history-short.png'],
    [390, 500, 'mobile-history-short.png'],
    [390, 320, 'mobile-history-landscape.png'],
  ]) {
    await paginated.setViewportSize({width, height});
    // The media-query listener may close the default-open desktop panel after
    // setViewportSize resolves. Wait for that state before opening it on mobile.
    await paginated.waitForFunction(() => {
      const panel = document.getElementById('lab-assistant');
      const desktop = window.matchMedia('(min-width: 1400px)').matches;
      return panel.hidden || panel.getAttribute('role') === (
        desktop ? 'complementary' : 'dialog');
    });
    if (!await paginated.locator('#lab-assistant').isVisible()) await paginated.locator('#assistant-launcher').click();
    await paginated.locator('#lab-assistant').waitFor({state:'visible'});
    await paginated.mouse.move(0, 0);
    await paginated.locator('#assistant-input-hint').waitFor({state:'hidden'});
    if (!await paginated.locator('#assistant-chat-menu').isVisible()) await paginated.locator('#assistant-conversations').click();
    await checkSearchStyle(width);
    const box = await paginated.locator('#assistant-chat-menu').boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= width + 1, `History fits ${width}×${height} width`);
    assert.ok(box.y >= 0 && box.y + box.height <= height + 1, `History fits ${width}×${height} height`);
    assert.equal(await paginated.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
      `History does not overflow at ${width}×${height}`);
    await paginated.screenshot({path:join(artifacts,filename)});
    await paginated.locator('#assistant-chat-close').focus();
    await paginated.keyboard.press('Shift+Tab');
    assert.equal(await paginated.locator('#assistant-chat-menu').isVisible(), false,
      'Returning keyboard focus to the history trigger dismisses the overlay');
    assert.equal(await paginated.locator('#assistant-conversations').evaluate(node => node === document.activeElement), true);
    await paginated.keyboard.press('Enter');
  }
  await paginated.close();
  await formPage.setViewportSize({width:1440,height:1000});
  await formPage.locator('#assistant-new').click();
  await formPage.locator('#assistant-conversations').click();
  await formPage.locator('#lab-assistant').screenshot({path:join(artifacts,'history-preview.png')});
  assert.deepEqual(errors, []);
  console.log('Assistant browser checks passed: history search, keyboard, polling, pagination, short viewports, saved chats, reload, tab closure, cancellation, rendering, resizing, mobile, and form context.');
} finally {
  await browser?.close();
  if (server && server.exitCode === null) { server.kill('SIGTERM'); await stopped; }
  rmSync(scratch, {recursive:true,force:true});
}
