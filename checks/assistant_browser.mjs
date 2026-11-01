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
  assert.deepEqual(errors, []);
  console.log('Assistant browser checks passed: desktop/tablet/mobile, Markdown tables and safe rendering, pointer/keyboard resizing, unsaved form context and limits, fresh retry snapshots, width and history persistence, sources, retry, cancellation, bounded history, keyboard/modal access and configuration.');
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
