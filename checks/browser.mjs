import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';

// Both Django and the browser run locally. Only this run's temporary DB is used.
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), 'lims-browser-'));
const env = {
  ...process.env,
  DJANGO_SETTINGS_MODULE: 'config.local', DEBUG: '1', DEMO_MODE: '0',
  LIMS_LOCAL_DB: join(scratch, 'browser.sqlite3'),
  LIMS_LOCAL_MEDIA: join(scratch, 'media'),
  SQLITE_PATH: join(scratch, 'browser.sqlite3'),
  ALLOWED_HOSTS: '127.0.0.1,localhost', PYTHONUNBUFFERED: '1',
};
delete env.DATABASE_URL;
delete env.SITE_PASSWORD_HASH;
const python = process.env.LIMS_PYTHON || join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const headed = process.argv.includes('--headed') || process.env.LIMS_HEADED === '1';
const firefox = process.argv.includes('--firefox');
const require = createRequire(import.meta.url);
const token = randomUUID();
let server, browser, serverClosed, artifacts, base;
let logs = '', cleanupPromise, interrupted = false;

const init = args => {
  const run = spawnSync(python, ['manage.py', ...args], {cwd: root, env, encoding: 'utf8'});
  if (run.error) throw new Error(`Cannot run ${python}. Set up Python dependencies first. ${run.error.message}`);
  assert.equal(run.status, 0, run.stdout + run.stderr);
};

async function checkArchiveDownload(page) {
  const digest = await page.locator('dl div').filter({
    has: page.locator('dt', {hasText: 'SHA-256 файла'}),
  }).locator('dd').innerText();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', {name: 'Скачать файл ↓', exact: true}).click(),
  ]);
  const bytes = readFileSync(await download.path());
  assert.deepEqual([...bytes.subarray(0, 4)], [0x50, 0x4b, 0x03, 0x04]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), digest);
  assert.equal(download.suggestedFilename(), '316 ЗИМНЯЯ УЛУЧШЕННАЯ.xlsx');
}

function carriedProperty(page) {
  const origin = 'input[name$="-origin"][value="carried_forward"]';
  return page.locator('#properties-formset .formset-row').filter({
    has: page.locator(origin),
  });
}

async function assertCarriedProperty(page, donorId, note) {
  const row = carriedProperty(page);
  assert.equal(await row.count(), 1, 'One explicitly carried-forward property');
  const selected = row.locator('input[name$="-source_cell"]');
  assert.equal(await selected.inputValue(), donorId,
    'Hidden evidence must retain the donor from another sheet');
  assert.equal(await selected.getAttribute('type'), 'hidden');
  assert.equal(await row.locator('[name$="-origin"]').getAttribute('type'), 'hidden');
  assert.equal(await row.locator('[name$="-provenance_note"]').getAttribute('type'), 'hidden');
  assert.equal(await row.locator('[name$="-property_code"]').inputValue(),
    'source_price_per_kg');
  assert.equal(Number(await row.locator('[name$="-value"]').inputValue()), 64.69);
  assert.equal(await row.locator('[name$="-provenance_note"]').inputValue(), note);
}

async function checkCarriedForwardFork(page, go, submit) {
  await go('/recipes/?q=302.84');
  const path = await page.getByRole('link', {name: '302.84', exact: true})
    .getAttribute('href');
  await go(path);
  const donorLink = page.getByRole('link', {name: '302.83!C12', exact: true});
  const donorId = (await donorLink.getAttribute('href')).match(/#cell-(\d+)$/)?.[1];
  assert.ok(donorId, 'The completed TEP-Z price points to its actual donor cell');
  const note = await donorLink.locator('..').locator('.cell-subtitle').innerText();
  assert.match(note, /По указанию пользователя/);
  await go(path + 'fork/');
  await assertCarriedProperty(page, donorId, note);
  await page.locator('[data-add-form="properties-formset"]').click();
  await page.waitForLoadState('networkidle');
  await assertCarriedProperty(page, donorId, note);
  await page.locator('#id_version_label').fill('QA-TEP-PROVENANCE');
  await page.locator('#id_status').selectOption('draft');
  await submit('Сохранить рецептуру');
  const fork = new URL(page.url()).pathname;
  await go(fork + 'edit/');
  await assertCarriedProperty(page, donorId, note);
  assert.equal(await page.locator('#id_source_sheet').getAttribute('type'), 'hidden');
  assert.equal(await page.locator('.formset-source, select[data-source-cell]').count(), 0);
  console.log('302.84 fork preserves donor selection and carried-forward provenance.');
}

// Let the OS bind port 0 in the actual server process, avoiding a free-port race.
// An explicit occupied port fails at bind; no browser request is sent to it.
const pythonServer = `
import json, os, sys
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True
application = StaticFilesHandler(get_wsgi_application())
with make_server('127.0.0.1', int(sys.argv[1]), application, server_class=Server) as httpd:
    print('LIMS_BROWSER_READY:' + json.dumps({'token': sys.argv[2], 'port': httpd.server_port}), flush=True)
    httpd.serve_forever()
`;

async function startServer(port) {
  server = spawn(python, ['-c', pythonServer, String(port), token], {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe']});
  serverClosed = new Promise(resolve => server.once('close', resolve));
  await new Promise((resolveReady, reject) => {
    let output = '';
    const timeout = setTimeout(() => fail(new Error(`Local test server did not start. ${logs}`)), 15000);
    function finish() {
      clearTimeout(timeout);
      server.off('error', fail);
      server.off('exit', exited);
      server.stdout.off('data', stdout);
    }
    function fail(error) { finish(); reject(error); }
    function exited(code, signal) { fail(new Error(`Local test server exited (${signal || code}). ${logs}`)); }
    function stdout(data) {
      output += data;
      for (const line of output.split('\n')) {
        if (!line.startsWith('LIMS_BROWSER_READY:')) continue;
        try {
          const ready = JSON.parse(line.slice('LIMS_BROWSER_READY:'.length));
          if (ready.token !== token || !Number.isInteger(ready.port) || ready.port < 1 || ready.port > 65535) continue;
          base = `http://127.0.0.1:${ready.port}`;
          finish(); resolveReady(); return;
        } catch { /* Wait for a complete line. */ }
      }
    }
    server.on('error', fail);
    server.on('exit', exited);
    server.stdout.on('data', stdout);
    server.stdout.on('data', data => { logs = (logs + data).slice(-12000); });
    server.stderr.on('data', data => { logs = (logs + data).slice(-12000); });
  });
  // A server that stops unexpectedly cannot be replaced by an unrelated server
  // and silently reused by the remaining test actions.
  server.once('exit', () => {
    if (!cleanupPromise) {
      console.error('The isolated test server stopped unexpectedly.');
      process.exitCode = 1;
      void cleanup();
    }
  });
  console.log(`Isolated test server: ${base}`);
}

function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    try { await browser?.close(); }
    finally {
      if (server) {
        if (server.exitCode === null && server.signalCode === null) server.kill('SIGTERM');
        const force = setTimeout(() => {
          if (server.exitCode === null && server.signalCode === null) server.kill('SIGKILL');
        }, 3000);
        try { await serverClosed; }
        finally { clearTimeout(force); }
      }
      // SQLite is removed only once the owning server has completely exited.
      rmSync(scratch, {recursive:true, force:true});
    }
  })();
  return cleanupPromise;
}

const signalHandlers = new Map(['SIGINT', 'SIGTERM'].map(signal => {
  const handler = () => {
    interrupted = true;
    process.exitCode = signal === 'SIGINT' ? 130 : 143;
    console.error(`Stopping local browser checks (${signal}).`);
    void cleanup();
  };
  process.on(signal, handler);
  return [signal, handler];
}));

const option = async (page, selector, label) => {
  const value = await page.locator(`${selector} option`).evaluateAll((items, label) => items.find(item => item.textContent.includes(label))?.value, label);
  assert.ok(value, `Option containing ${label} in ${selector}`);
  await page.locator(selector).selectOption(value);
  return value;
};

const searchableSelect = (page, selector) => (
  page.locator(`.search-select:has(${selector})`)
);

async function checkSelectSearch(page, go) {
  await go('/recipes/new/');
  const mixture = searchableSelect(page, '#id_mixture_type');
  await mixture.locator('.search-select-trigger').click();
  const input = mixture.locator('.search-select-input');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);
  await input.fill('УЛУЧШЕН');
  assert.equal(await mixture.getByRole('option').count(), 1);
  assert.match(await mixture.getByRole('option').innerText(), /улучшенная/);
  await input.press('ArrowDown');
  await input.press('Enter');
  assert.match(await mixture.locator('select option:checked').innerText(),
    /Зимняя улучшенная пена/);
  await checkMaterialSearch(page);
  await checkAddedSelect(page);
  await page.screenshot({path: join(artifacts, 'searchable-form.png'), fullPage: true});
  await checkExperimentRecipeSelect(page, go);
  await checkRequiredSelect(page, go);
  console.log('Searchable forms: Cyrillic infix, separator aliases, keyboard, clear and dynamic rows passed.');
}

async function checkMaterialSearch(page) {
  const material = searchableSelect(page, '#id_components-0-material');
  const trigger = material.locator('.search-select-trigger');
  const input = material.locator('.search-select-input');
  await trigger.click();
  for (const query of ['EP Z', 'ep_z', 'ep-z', 'ep.z', 'ep - z', 'ep__.. - z']) {
    await input.fill(query);
    assert.equal(await material.getByRole('option').count(), 1, query);
    assert.equal(await material.getByRole('option').innerText(), 'TEP-Z');
  }
  await page.screenshot({path: join(artifacts, 'searchable-dropdown.png')});
  await input.press('ArrowDown');
  await input.press('Enter');
  const selected = await material.locator('select').inputValue();
  assert.ok(selected);
  assert.match(await trigger.innerText(), /TEP-Z/);
  await trigger.click();
  await input.fill('совсем-нет-такого-компонента');
  assert.equal(await material.getByRole('option').count(), 0);
  assert.equal(await material.locator('.search-select-empty').isVisible(), true);
  await input.press('Escape');
  assert.equal(await material.locator('select').inputValue(), selected);
  assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
  await trigger.click();
  await clearSelectWithKeyboard(material);
}

async function clearSelectWithKeyboard(field) {
  const input = field.locator('.search-select-input');
  const clear = field.locator('.search-select-clear');
  await input.press('Tab');
  assert.equal(await clear.evaluate(el => el === document.activeElement), true);
  await clear.press('Shift+Tab');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);
  await input.press('Tab');
  await clear.press('Enter');
  assert.equal(await field.locator('select').inputValue(), '');
}

async function checkAddedSelect(page) {
  await page.locator('[data-add-form="components-formset"]').click();
  const material = searchableSelect(page, '#id_components-1-material');
  await material.locator('.search-select-trigger').click();
  await material.locator('.search-select-input').fill('ovol c21');
  const candidate = material.getByRole('option', {name: 'Inovol C210', exact: true});
  assert.equal(await candidate.count(), 1);
  await candidate.click();
  assert.ok(await material.locator('select').inputValue());
  await material.locator('.search-select-trigger').click();
  await material.locator('.search-select-input').press('Tab');
  const clear = material.locator('.search-select-clear');
  assert.equal(await clear.evaluate(el => el === document.activeElement), true);
  await clear.press('Tab');
  assert.equal(await material.locator('.search-select-popover').isVisible(), false);
}

async function checkMobileSelect(page, width) {
  const mixture = searchableSelect(page, '#id_mixture_type');
  await mixture.locator('.search-select-trigger').click();
  const popup = mixture.locator('.search-select-popover');
  const bounds = await popup.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width,
    `Search dropdown fits ${width}px viewport`);
  await mixture.locator('.search-select-input').fill('УЛУЧШЕН');
  await mixture.getByRole('option').click();
  assert.equal(await page.evaluate(() => (
    document.documentElement.scrollWidth > innerWidth
  )), false, `Open searchable dropdown: ${width}px overflow`);
}

async function checkExperimentRecipeSelect(page, go) {
  await go('/experiments/new/');
  assert.equal(await page.locator('#conditions-formset').count(), 0);
  const field = searchableSelect(page, '#id_recipe');
  const trigger = field.locator('.search-select-trigger');
  await trigger.focus();
  await trigger.press('ArrowDown');
  const input = field.locator('.search-select-input');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);
  await input.press('Escape');
}

async function checkFormsetRemoval(page, go, submit) {
  const rows = prefix => page.locator(`#${prefix}-formset .formset-row`);
  const add = prefix => page.locator(`[data-add-form="${prefix}-formset"]`).click();
  const remove = (prefix, index) => rows(prefix).nth(index).locator('[data-remove-form]').click();
  const checkCounts = async (prefix, count, initial) => {
    assert.equal(await rows(prefix).count(), count, prefix);
    assert.equal(await page.locator(`#id_${prefix}-TOTAL_FORMS`).inputValue(), String(count));
    assert.equal(await page.locator(`#id_${prefix}-INITIAL_FORMS`).inputValue(), String(initial));
  };
  const clear = async prefix => {
    while (await rows(prefix).count()) await remove(prefix, 0);
    await checkCounts(prefix, 0, 0);
    assert.equal(await page.locator(`[data-add-form="${prefix}-formset"]`)
      .evaluate(button => button === document.activeElement), true);
  };

  await go('/recipes/new/');
  const prefixes = ['components', 'filling', 'properties'];
  for (const prefix of prefixes) {
    await checkCounts(prefix, 1, 0);
    await add(prefix);
    await add(prefix);
  }
  assert.equal(await page.locator('[name$="-DELETE"], .formset-source').count(), 0);
  assert.doesNotMatch(await page.locator('main').innerText(), /Первичный источник|Первичная ячейка|Лист первичного источника/);
  const material = await page.locator('#id_components-0-material option')
    .evaluateAll(options => options.find(option => option.value)?.value);
  assert.ok(material);
  await page.locator('#id_version_label').fill('QA-FORMSET-REMOVE');
  for (let index = 0; index < 3; index++) {
    await page.locator(`#id_components-${index}-material`).selectOption(material);
    await page.locator(`#id_components-${index}-quantity_value`).fill(index === 1 ? '-1' : String((index + 1) * 10));
    await page.locator(`#id_filling-${index}-group_label`).fill('A');
    await page.locator(`#id_filling-${index}-mass_g`).fill(String((index + 1) * 10));
    await page.locator(`#id_properties-${index}-property_code`).fill(`qa_property_${index}`);
    await page.locator(`#id_properties-${index}-value`).fill(String(index + 1));
  }
  for (const prefix of prefixes) {
    await remove(prefix, 1);
    await checkCounts(prefix, 2, 0);
  }
  assert.equal(await page.locator('#id_components-1-quantity_value').inputValue(), '30');
  const selected = searchableSelect(page, '#id_components-1-material');
  await selected.locator('.search-select-trigger').click();
  await selected.locator('.search-select-input').press('Escape');
  assert.equal(await selected.locator('select').inputValue(), material);

  // Deleted rows stay absent when another field fails server validation.
  await page.locator('#id_components-0-group_label').fill('');
  await Promise.all([
    page.waitForNavigation({waitUntil: 'networkidle'}),
    page.getByRole('button', {name: 'Сохранить рецептуру', exact: true}).click(),
  ]);
  assert.match((await page.locator('.field-error').allTextContents()).join(' '), /Укажите группу/);
  for (const prefix of prefixes) await checkCounts(prefix, 2, 0);
  await page.locator('#id_components-0-group_label').fill('A');
  await submit('Сохранить рецептуру');
  const recipe = new URL(page.url()).pathname;
  await go(recipe + 'edit/');
  for (const prefix of prefixes) {
    await checkCounts(prefix, 3, 2);
    await remove(prefix, 0);
    await remove(prefix, 1); // Remove the default empty row as well.
    await checkCounts(prefix, 1, 1);
  }
  await submit('Сохранить изменения');
  await go(recipe + 'edit/');
  for (const prefix of prefixes) await checkCounts(prefix, 2, 1);
  assert.equal(await page.locator('#id_components-0-quantity_value').inputValue(), '30');
  assert.equal(await page.locator('#id_filling-0-mass_g').inputValue(), '30');
  assert.equal(await page.locator('#id_properties-0-property_code').inputValue(), 'qa_property_2');
  for (const prefix of prefixes) await clear(prefix);
  await submit('Сохранить изменения');
  await go(recipe + 'edit/');
  for (const prefix of prefixes) await checkCounts(prefix, 1, 0);

  await go('/recipe-templates/new/');
  await page.locator('#id_name').fill('QA удаление строк шаблона');
  for (const prefix of ['components', 'filling']) {
    await checkCounts(prefix, 1, 0);
    await clear(prefix);
    await add(prefix); // Adding again after deleting the last row works.
    await add(prefix);
    await add(prefix);
  }
  for (let index = 0; index < 3; index++) {
    await page.locator(`#id_components-${index}-material`).selectOption(material);
    await page.locator(`#id_components-${index}-quantity_value`).fill(String(index + 1));
    await page.locator(`#id_filling-${index}-group_label`).fill('A');
    await page.locator(`#id_filling-${index}-mass_g`).fill(String(index + 1));
  }
  for (const prefix of ['components', 'filling']) await remove(prefix, 1);
  await submit('Сохранить шаблон рецептуры');
  const template = new URL(page.url()).pathname;
  await go(template + 'fork/');
  for (const prefix of ['components', 'filling']) {
    await checkCounts(prefix, 3, 2);
    await remove(prefix, 0);
    await remove(prefix, 1);
    await checkCounts(prefix, 1, 1);
  }
  await submit('Сохранить шаблон рецептуры');
  await go(new URL(page.url()).pathname + 'fork/');
  for (const prefix of ['components', 'filling']) await checkCounts(prefix, 2, 1);
  assert.equal(await page.locator('#id_components-0-quantity_value').inputValue(), '3');
  assert.equal(await page.locator('#id_filling-0-mass_g').inputValue(), '3');
  console.log('Formsets: one default row, physical removal, reindexing, last-row removal, validation retry and saved recipe/template deletion passed.');
}

async function checkFormDrafts(context) {
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const visit = path => page.goto(base + path, {waitUntil: 'networkidle'});
  const add = prefix => page.locator(`[data-add-form="${prefix}-formset"]`).click();
  const remove = (prefix, index) => page.locator(`#${prefix}-formset [data-remove-form]`).nth(index).click();
  const snapshot = () => page.evaluate(() => {
    const form = document.querySelector('[data-formset-prefix], form[data-form-draft]').closest('form');
    return {
      fields: [...new FormData(form)].filter(([name]) => name !== 'csrfmiddlewaretoken'),
      dates: [...form.querySelectorAll('.date-picker input[type="text"]')].map(input => input.value),
      rows: [...form.querySelectorAll('[data-formset-prefix]')].map(group => [
        group.dataset.formsetPrefix, group.querySelectorAll('.formset-row').length,
      ]),
    };
  });
  const reload = async () => {
    const before = await snapshot();
    await page.reload({waitUntil: 'networkidle'});
    assert.deepEqual(await snapshot(), before, 'Reload preserves fields, dates and every row');
  };
  const submit = async label => {
    await Promise.all([
      page.waitForNavigation({waitUntil: 'networkidle'}),
      page.getByRole('button', {name: label, exact: true}).click(),
    ]);
  };

  await visit('/recipes/new/');
  await page.locator('#id_version_label').fill('QA-REFRESH-DRAFT');
  await page.locator('#id_title').fill('Черновик перед обновлением');
  await page.locator('#id_notes').fill('Первая строка\nВторая строка');
  await page.locator('#id_recorded_on').fill('03.11.2026');
  const material = await page.locator('#id_components-0-material option')
    .evaluateAll(options => options.find(option => option.value)?.value);
  for (const prefix of ['components', 'filling', 'properties']) {
    await add(prefix);
    await add(prefix);
  }
  for (let index = 0; index < 3; index++) {
    await page.locator(`#id_components-${index}-material`).selectOption(material);
    await page.locator(`#id_components-${index}-quantity_value`).fill(String(index * 10));
    await page.locator(`#id_filling-${index}-group_label`).fill('A');
    await page.locator(`#id_filling-${index}-mass_g`).fill(String(index * 20));
    await page.locator(`#id_properties-${index}-property_code`).fill(`refresh_${index}`);
    await page.locator(`#id_properties-${index}-value`).fill(String(index));
  }
  await reload();
  for (const prefix of ['components', 'filling', 'properties']) await remove(prefix, 1);
  await reload();
  assert.equal(await page.locator('#id_components-1-quantity_value').inputValue(), '20');
  assert.match(await searchableSelect(page, '#id_components-1-material')
    .locator('.search-select-trigger').innerText(), /\S/);
  await page.locator('#id_recorded_on').fill('03.11.20__');
  await reload();
  await page.locator('#id_recorded_on').fill('03.11.2026');

  // Leaving the editor and opening it again in this tab also retains the draft.
  await visit('/recipes/');
  await visit('/recipes/new/');
  assert.equal(await page.locator('#id_components-TOTAL_FORMS').inputValue(), '2');
  assert.equal(await page.locator('#id_title').inputValue(), 'Черновик перед обновлением');
  const independent = await context.newPage();
  await independent.goto(base + '/recipes/new/', {waitUntil: 'networkidle'});
  assert.equal(await independent.locator('#id_components-TOTAL_FORMS').inputValue(), '1');
  assert.equal(await independent.locator('#id_title').inputValue(), '');
  await independent.close();

  await page.locator('#id_components-0-group_label').fill('');
  await submit('Сохранить рецептуру');
  assert.match((await page.locator('.field-error').allTextContents()).join(' '), /Укажите группу/);
  await page.locator('#id_title').fill('Исправление после ошибки');
  await reload();
  await page.locator('#id_components-0-group_label').fill('A');
  await submit('Сохранить рецептуру');
  assert.equal(await page.locator('.errorlist,.field-error').count(), 0);
  const recipe = new URL(page.url()).pathname;
  assert.match(recipe, /^\/recipes\/\d+\/$/);
  await visit('/recipes/new/');
  assert.equal(await page.locator('#id_components-TOTAL_FORMS').inputValue(), '1');
  assert.equal(await page.locator('#id_version_label').inputValue(), '');

  await visit(recipe + 'edit/');
  await remove('components', 0);
  await remove('filling', 0);
  await remove('properties', 0);
  await reload();
  await submit('Сохранить изменения');
  await visit(recipe + 'edit/');
  assert.equal(await page.locator('#id_components-INITIAL_FORMS').inputValue(), '1');
  assert.equal(await page.locator('#id_components-0-quantity_value').inputValue(), '20');

  // A newer server record wins over an older browser draft.
  await page.locator('#id_title').fill('Старое локальное изменение');
  const payload = await page.evaluate(() => Object.fromEntries(new FormData(
    document.querySelector('[data-formset-prefix], form[data-form-draft]').closest('form'),
  )));
  payload.title = 'Сохранено другим запросом';
  const response = await page.request.post(base + recipe + 'edit/', {
    form: payload, headers: {Referer: page.url()},
  });
  assert.equal(response.status(), 200);
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(await page.locator('#id_title').inputValue(), payload.title);

  for (const [path, prefixes, field, value] of [
    ['/recipe-templates/new/', ['components', 'filling'], '#id_name', 'Черновик шаблона'],
    ['/experiments/new/', [], '#id_title', 'Черновик эксперимента'],
  ]) {
    await visit(path);
    await page.locator(field).fill(value);
    for (const prefix of prefixes) {
      await add(prefix);
      await add(prefix);
      await remove(prefix, 0);
    }
    if (path.startsWith('/experiments/')) {
      await page.locator('#id_recorded_tested_on').fill('04.11.2026');
    } else {
      await page.locator('#id_components-1-material').selectOption(material);
      await page.locator('#id_components-1-quantity_value').fill('0');
    }
    await reload();
    for (const prefix of prefixes) {
      while (await page.locator(`#${prefix}-formset .formset-row`).count()) await remove(prefix, 0);
    }
    await reload();
    for (const prefix of prefixes) await add(prefix);
    await reload();
    await page.locator('.form-actions a').click();
    await page.waitForLoadState('networkidle');
    await visit(path);
    assert.equal(await page.locator(field).inputValue(), '');
    for (const prefix of prefixes) {
      assert.equal(await page.locator(`#id_${prefix}-TOTAL_FORMS`).inputValue(), '1');
    }
  }

  // Match the reported Ctrl+R case on an existing card in both engines.
  await visit('/experiments/new/');
  const publishedRecipe = await page.locator('#id_recipe option')
    .evaluateAll(options => options.find(option => option.value)?.value);
  await page.locator('#id_recipe').selectOption(publishedRecipe);
  await page.locator('#id_title').fill('QA Ctrl+R существующей карточки');
  await submit('Сохранить эксперимент');
  assert.equal(await page.locator('.errorlist,.field-error').count(), 0);
  const card = new URL(page.url()).pathname;
  await visit(card + 'edit/');
  await reload();
  await page.locator('#id_notes').fill('Введено до Ctrl+R\nНе сохранено в карточку');
  const beforeShortcut = await snapshot();
  await page.reload({waitUntil: 'networkidle'});
  assert.deepEqual(await snapshot(), beforeShortcut,
    'Ctrl+R on an existing card retains filled rows and notes');
  await reload();

  await visit(card + 'measurement-groups/new/');
  const availableGroup = await page.locator('#id_measurement_group option')
    .evaluateAll(options => options.find(option => option.value)?.value);
  await page.locator('#id_measurement_group').selectOption(availableGroup);
  await submit('Продолжить');
  const groupMetric = page.locator('input[name^="metric_"]').first();
  await groupMetric.fill('0');
  for (const reason of await page.locator('input[name$="_reason"]').all()) {
    const name = (await reason.getAttribute('name')).replace(/_reason$/, '');
    if (await page.locator(`[name="${name}"]`).inputValue() === '') {
      await reason.fill('Не было оборудования');
    }
  }
  await reload();
  assert.equal(await groupMetric.inputValue(), '0');
  await submit('Сохранить группу измерений');
  assert.match(await page.locator('main').innerText(), /Группа измерений сохранена/);

  // Hidden source links can also be restored by Firefox after row deletion.
  await visit('/recipes/?q=302.84');
  const archive = await page.getByRole('link', {name: '302.84', exact: true}).getAttribute('href');
  await visit(archive + 'fork/');
  await remove('components', 0);
  await remove('properties', 0);
  await reload();
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Drafts: refresh, fields/dates, added/deleted/empty rows, validation retries, tab isolation, server changes, success and cancel passed.');
}

async function checkRequiredSelect(page, go) {
  await go('/lots/new/');
  await page.locator('#id_lot_code').fill('QA validation only');
  await page.getByRole('button', {name: 'Сохранить', exact: true}).click();
  const material = searchableSelect(page, '#id_material');
  const trigger = material.locator('.search-select-trigger');
  assert.equal(await trigger.getAttribute('aria-invalid'), 'true');
  assert.equal(await trigger.evaluate(el => el === document.activeElement), true,
    'A required select focuses its visible field after validation');
  assert.equal(await material.locator('.field-error').isVisible(), true);
  assert.equal(new URL(page.url()).pathname, '/lots/new/');
  await trigger.click();
  await material.locator('.search-select-input').fill('ovol c21');
  await material.getByRole('option', {name: 'Inovol C210', exact: true}).click();
  assert.equal(await trigger.getAttribute('aria-invalid'), 'false');
  assert.equal(await material.locator('.field-error').count(), 0);
}

async function changeStatus(page, submit, status) {
  const detail = new URL(page.url()).pathname;
  const action = page.getByRole('link', {name: 'Изменить статус', exact: true});
  assert.equal(await action.getAttribute('href'), detail + 'status/');
  await Promise.all([
    page.waitForNavigation({waitUntil: 'networkidle'}), action.click(),
  ]);
  assert.equal(await page.locator('#id_current_status').count(), 1);
  const select = searchableSelect(page, '#id_status');
  await select.locator('.search-select-trigger').click();
  const label = await select.locator(`option[value="${status}"]`).innerText();
  await select.locator('.search-select-input').fill(label);
  await select.getByRole('option', {name: label, exact: true}).click();
  await submit('Сохранить статус');
  assert.equal(new URL(page.url()).pathname, detail);
  await page.reload({waitUntil: 'networkidle'});
  assert.match(await page.locator('.detail-meta,.page-heading').first().innerText(),
    new RegExp(label));
}

async function checkRegistrySearch(page, go) {
  for (const query of ['16_26', '16-26']) {
    await go('/recipes/?q=' + encodeURIComponent(query));
    assert.equal(await page.getByRole('link', {
      name: '316.26', exact: true,
    }).count(), 1, query);
  }
  await go('/materials/?q=ovol_c21');
  assert.equal(await page.locator('tbody tr').count(), 1);
  assert.match(await page.locator('tbody').innerText(), /Inovol C210/);
  await go('/material-categories/?q=' + encodeURIComponent('ОЛИОЛ'));
  assert.equal(await page.getByRole('link', {
    name: 'Полиолы', exact: true,
  }).count(), 1);
  console.log('Registry search finds infixes, separator aliases and Cyrillic case variants.');
}

const liveSearchInput = page => page.locator('form[data-live-search] input[name="q"]');

function liveSearchResponse(page, query) {
  const path = new URL(page.url()).pathname;
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return response.request().resourceType() === 'fetch'
      && url.pathname === path && (url.searchParams.get('q') || '') === query;
  });
}

async function waitLiveResults(page, query) {
  await page.waitForFunction(query => {
    const panel = document.querySelector('[data-live-results]');
    return panel && panel.getAttribute('aria-busy') !== 'true'
      && (new URL(location.href).searchParams.get('q') || '') === query;
  }, query);
}

async function typeLiveQuery(page, query) {
  const input = liveSearchInput(page), response = liveSearchResponse(page, query);
  await input.evaluate(node => { window.registrySearchInput = node; });
  await input.fill(query);
  await input.evaluate(node => node.setSelectionRange(1, 1));
  await response;
  await waitLiveResults(page, query);
  const state = await input.evaluate(node => ({
    same: node === window.registrySearchInput,
    focused: node === document.activeElement,
    start: node.selectionStart, end: node.selectionEnd,
  }));
  assert.deepEqual(state, {same: true, focused: true, start: 1, end: 1},
    'Live results preserve the original input, focus and caret');
}

async function clickLiveLink(page, link, query) {
  const response = liveSearchResponse(page, query);
  await link.click();
  await response;
  await waitLiveResults(page, query);
}

async function navigateLiveHistory(page, direction, query) {
  const response = liveSearchResponse(page, query);
  if (direction === 'back') await page.goBack();
  else await page.goForward();
  await response;
  await waitLiveResults(page, query);
}

async function checkLiveMaterials(page, go) {
  await go('/materials/');
  for (const query of ['EP Z', 'ep_z', 'ep-z', 'ep.z', 'ep - z']) {
    await typeLiveQuery(page, query);
    assert.equal(await page.locator('tbody tr').count(), 1);
    assert.equal(await page.locator('tbody tr td').first().innerText(), 'TEP-Z');
  }
  await typeLiveQuery(page, 'ovol_c21');
  assert.match(await page.locator('tbody').innerText(), /Inovol C210/);
  const reset = page.locator('form[data-live-search]')
    .getByRole('link', {name: 'Сбросить', exact: true});
  assert.equal(await reset.count(), 1);
  await clickLiveLink(page, reset, '');
  assert.equal(await liveSearchInput(page).inputValue(), '');
  assert.equal(await reset.count(), 0);
  assert.ok(await page.locator('tbody tr').count() > 1);
  console.log('Materials update while typing with equivalent separators and preserved focus.');
}

async function checkLiveRecipes(page, go) {
  await go('/recipes/?status=published');
  await clickLiveLink(page,
    page.getByRole('link', {name: 'Следующая страница', exact: true}), '');
  assert.equal(new URL(page.url()).searchParams.get('page'), '2');
  await typeLiveQuery(page, '16 26');
  assert.equal(new URL(page.url()).searchParams.has('page'), false);
  assert.equal(new URL(page.url()).searchParams.get('status'), 'published');
  assert.equal(await page.getByRole('link', {name: '316.26', exact: true}).count(), 1);
  await clickLiveLink(page, page.locator('form[data-live-search]')
    .getByRole('link', {name: 'Сбросить', exact: true}), '');
  await navigateLiveHistory(page, 'back', '16 26');
  assert.equal(await liveSearchInput(page).inputValue(), '16 26');
  assert.equal(await page.locator('select[name="status"]').inputValue(), 'published');
  assert.equal(await page.getByRole('link', {name: '316.26', exact: true}).count(), 1);
  await navigateLiveHistory(page, 'forward', '');
  assert.equal(await liveSearchInput(page).inputValue(), '');
  assert.equal(await page.locator('select[name="status"]').inputValue(), '');
  const response = liveSearchResponse(page, '302_84');
  await liveSearchInput(page).fill('302_84');
  await liveSearchInput(page).press('Enter');
  await response;
  await waitLiveResults(page, '302_84');
  assert.equal(await page.getByRole('link', {name: '302.84', exact: true}).count(), 1);
  console.log('Live recipe filters, pagination reset, reset link, Enter and Back/Forward passed.');
}

async function checkLiveTemplatesAndHistory(page, go) {
  for (const [path, query, title] of [
    ['/recipe-templates/', 'qa_шаблон.зимней-пены', 'QA шаблон зимней пены'],
  ]) {
    await go(path);
    await typeLiveQuery(page, query);
    assert.equal(await page.locator('tbody tr').count(), 2);
    assert.match(await page.locator('tbody').innerText(), new RegExp(title));
  }
  await go('/history/?entity=test');
  await typeLiveQuery(page, 'qa 316 1');
  assert.equal(new URL(page.url()).searchParams.get('entity'), 'test');
  assert.match(await page.locator('.timeline').innerText(), /QA-316.1/);
  console.log('Template registries and filtered history update without Enter.');
}

async function checkDirtyLivePagination(page, go) {
  await go('/recipes/?status=published');
  const response = liveSearchResponse(page, '16 26');
  await liveSearchInput(page).evaluate(input => {
    input.value = '16 26';
    input.dispatchEvent(new Event('input', {bubbles: true}));
    document.querySelector('.pagination a[aria-label="Следующая страница"]').click();
  });
  await response;
  await waitLiveResults(page, '16 26');
  assert.equal(await liveSearchInput(page).inputValue(), '16 26');
  assert.equal(new URL(page.url()).searchParams.has('page'), false);
  assert.equal(new URL(page.url()).searchParams.get('status'), 'published');
  assert.equal(await page.getByRole('link', {name: '316.26', exact: true}).count(), 1);
  console.log('Clicking an old pagination link preserves a newly typed query.');
}

async function ignoreStaleFetchAbort(page) {
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = new URL(typeof resource === 'string' ? resource
        : resource.url || String(resource), location.href);
      if (url.pathname === '/materials/' && url.searchParams.get('q') === 'TEP-Z') {
        return nativeFetch(resource, {...options, signal: undefined});
      }
      return nativeFetch(resource, options);
    };
  });
}

async function holdMaterialResponse(page) {
  let requested, release, done;
  const waiting = new Promise(resolve => { requested = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const delivered = new Promise(resolve => { done = resolve; });
  await page.route(/\/materials\/(?:\?|$)/, async route => {
    const query = new URL(route.request().url()).searchParams.get('q');
    if (route.request().resourceType() !== 'fetch' || query !== 'TEP-Z') {
      return route.continue();
    }
    const response = await route.fetch();
    requested();
    await gate;
    try { await route.fulfill({response}); }
    finally { done(); }
  });
  return {waiting, release, delivered};
}

async function waitTwoFrames(page) {
  await page.evaluate(() => new Promise(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
}

async function checkStaleLiveResponse(page) {
  const target = await page.context().newPage();
  const errors = [];
  target.on('pageerror', error => errors.push(error.message));
  const held = await holdMaterialResponse(target);
  try {
    await ignoreStaleFetchAbort(target);
    await target.goto(base + '/materials/', {waitUntil: 'networkidle'});
    await liveSearchInput(target).fill('TEP-Z');
    await held.waiting;
    await typeLiveQuery(target, 'Inovol_C210');
    const staleResponse = liveSearchResponse(target, 'TEP-Z');
    held.release();
    await staleResponse;
    await held.delivered;
    await waitTwoFrames(target);
    assert.equal(new URL(target.url()).searchParams.get('q'), 'Inovol_C210');
    assert.match(await target.locator('tbody').innerText(), /Inovol C210/);
    assert.doesNotMatch(await target.locator('tbody').innerText(), /TEP-Z/);
    assert.deepEqual(errors, []);
  } finally { held.release(); await target.close(); }
  console.log('A delayed response cannot overwrite the latest search even if abort is ignored.');
}

async function checkPendingPageRestore(page) {
  const target = await page.context().newPage();
  const held = await holdMaterialResponse(target);
  try {
    await ignoreStaleFetchAbort(target);
    await target.goto(base + '/materials/?category=Полиолы', {waitUntil: 'networkidle'});
    await liveSearchInput(target).fill('TEP-Z');
    await held.waiting;
    const panel = target.locator('[data-live-results]');
    assert.equal(await panel.getAttribute('aria-busy'), 'true');
    await target.evaluate(() => dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true})));
    assert.equal(await panel.getAttribute('aria-busy'), 'false');
    const restored = liveSearchResponse(target, '');
    await target.evaluate(() => dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
    await restored;
    await waitLiveResults(target, '');
    assert.equal(await liveSearchInput(target).inputValue(), '');
    assert.equal(await target.locator('select[name="category"]').inputValue(), 'Полиолы');
    assert.ok(await target.locator('tbody tr').count() > 1);
    const stale = liveSearchResponse(target, 'TEP-Z');
    held.release();
    await stale;
    await held.delivered;
    await waitTwoFrames(target);
    assert.equal(await panel.getAttribute('aria-busy'), 'false');
  } finally { held.release(); await target.close(); }
  console.log('A restored cached page clears pending loading and reloads URL filters/results.');
}

async function assertLiveSearchMarkers(page) {
  const forms = page.locator('form.filter-bar:has(input[name="q"])');
  for (const form of await forms.all()) {
    assert.equal(await form.getAttribute('data-live-search'), '');
    assert.equal(await form.evaluate(node => (
      node.closest('.panel')?.hasAttribute('data-live-results')
    )), true);
  }
}

async function sampleSidebarFrames(page) {
  await page.addInitScript(() => {
    sessionStorage.setItem('lims.sidebar.scrollTop', '280');
    window.sidebarFrames = [];
    function sample() {
      const navigation = document.querySelector('#sidebar nav');
      if (navigation && navigation.clientHeight > 0) {
        window.sidebarFrames.push({
          top: navigation.scrollTop,
          max: navigation.scrollHeight - navigation.clientHeight,
          linkHeight: navigation.querySelector('a').getBoundingClientRect().height,
        });
      }
      if (window.sidebarFrames.length < 120) requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  });
  await page.route(/\/static\/lab\/(app|search-select)\.js(?:\?|$)/,
    async route => {
      await new Promise(resolve => setTimeout(resolve, 500));
      await route.continue();
    });
}

async function checkSidebarFirstPaint(browser, storageState) {
  const context = await browser.newContext({storageState});
  const page = await context.newPage();
  try {
    await sampleSidebarFrames(page);
    for (const viewport of [
      {width: 1440, height: 620}, {width: 768, height: 820},
      {width: 390, height: 680},
    ]) {
      await page.setViewportSize(viewport);
      await page.goto(base + '/recipes/', {waitUntil: 'networkidle'});
      const frames = await page.evaluate(() => window.sidebarFrames);
      assert.ok(frames.length > 5, 'Sampled frames while main scripts were delayed');
      assert.ok(frames.every(frame => frame.max > 0
        && frame.top === Math.min(280, frame.max)),
        `${viewport.width}px sidebar must retain scroll from its first frame`);
      assert.equal(new Set(frames.map(frame => frame.linkHeight)).size, 1,
        `${viewport.width}px sidebar link height must remain stable`);
    }
  } finally { await context.close(); }
  console.log('Sidebar first paint retains scroll and geometry with delayed scripts.');
}

async function assertPickerFieldStyles(field) {
  assert.equal(await field.count(), 1, 'Loaded picker styles enable one custom control');
  const native = await field.locator('select').evaluate(select => {
    const style = getComputedStyle(select), box = select.getBoundingClientRect();
    return {position: style.position, opacity: style.opacity,
      width: box.width, height: box.height};
  });
  assert.equal(native.position, 'absolute');
  assert.equal(native.opacity, '0');
  assert.ok(native.width <= 1 && native.height <= 1,
    'The native select stays available to forms without occupying layout');
  const trigger = await field.locator('.search-select-trigger').evaluate(button => {
    const style = getComputedStyle(button);
    return {display: style.display, radius: parseFloat(style.borderRadius)};
  });
  assert.equal(trigger.display, 'flex');
  assert.ok(trigger.radius >= 8, 'Picker trigger has the application styling');
}

async function checkPickerAssetURLs(page) {
  const stylesheet = page.locator('link[href*="/lab/search-select.css"]');
  assert.equal(await stylesheet.count(), 1, 'Picker has its own stylesheet');
  const urls = await page.locator('link[href*="/static/lab/"],script[src*="/static/lab/"]')
    .evaluateAll(nodes => nodes.map(node => node.href || node.src)
      .filter(url => /\.(css|js)(?:\?|$)/.test(url)));
  assert.ok(urls.length >= 5);
  for (const url of urls) {
    assert.match(new URL(url).searchParams.get('v') || '', /^[a-f0-9]{16}$/,
      `Updated assets use content versions: ${url}`);
  }
}

async function assertStyledPopup(page, field, height) {
  const popup = field.locator('.search-select-popover');
  await popup.evaluate(node => Promise.all(node.getAnimations()
    .map(animation => animation.finished.catch(() => {}))));
  const style = await popup.evaluate(node => {
    const style = getComputedStyle(node);
    return {position: style.position, display: style.display,
      radius: parseFloat(style.borderRadius), background: style.backgroundColor,
      opacity: style.opacity};
  });
  assert.equal(style.position, 'fixed');
  assert.equal(style.display, 'flex');
  assert.ok(style.radius >= 8);
  assert.notEqual(style.background, 'rgba(0, 0, 0, 0)');
  assert.equal(style.opacity, '1');
  assert.equal(await page.locator('.search-select-popover:not([hidden])').count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollHeight),
    height, 'Opening a dropdown does not move the form fields');
}

async function captureRecipeTemplate(page, state) {
  await page.screenshot({
    path: join(artifacts, `recipe-template-form-${state}.png`),
    animations: 'disabled',
  });
}

async function checkRecipeTemplatePicker(page, go) {
  await go('/recipe-templates/new/');
  await checkPickerAssetURLs(page);
  const mixture = searchableSelect(page, '#id_mixture_type');
  await assertPickerFieldStyles(mixture);
  await captureRecipeTemplate(page, 'closed');
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await mixture.locator('.search-select-trigger').click();
  await assertStyledPopup(page, mixture, height);
  await captureRecipeTemplate(page, 'open');
  const material = searchableSelect(page, '#id_components-0-material');
  await material.locator('.search-select-trigger').click();
  await assertStyledPopup(page, material, height);
  assert.equal(await mixture.locator('.search-select-trigger')
    .getAttribute('aria-expanded'), 'false');
  await mixture.locator('.search-select-trigger').click();
  await mixture.locator('.search-select-input').fill('УЛУЧШЕН');
  assert.equal(await mixture.getByRole('option').count(), 1);
  await captureRecipeTemplate(page, 'filtered');
  await mixture.locator('.search-select-input').press('Enter');
  assert.match(await mixture.locator('select option:checked').innerText(), /улучшенная/);
  await assertPickerFieldStyles(material);
  await material.locator('.search-select-trigger').click();
  await assertStyledPopup(page, material, height);
  await material.locator('.search-select-input').press('Escape');
  console.log('Recipe-template pickers have versioned styles, one floating popup and stable layout.');
}

async function checkPickerCSSFallback(browser, storageState) {
  const context = await browser.newContext({storageState});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.route(/\/static\/lab\/search-select\.css(?:\?|$)/,
      route => route.fulfill({contentType: 'text/css', body: ''}));
    await page.goto(base + '/recipe-templates/new/', {waitUntil: 'networkidle'});
    assert.equal(await page.locator('.search-select').count(), 0,
      'A missing picker stylesheet leaves a single native control');
    assert.equal(await page.locator('#id_mixture_type').evaluate(select => (
      getComputedStyle(select).opacity
    )), '1');
    await option(page, '#id_mixture_type', 'Зимняя улучшенная пена');
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
  console.log('Missing picker CSS keeps native forms usable without duplicate controls.');
}

function assertFeedbackSaved(text, employeeName) {
  const query = `Feedback.objects.filter(text=${JSON.stringify(text)})`;
  init(['shell', '-c', [
    'from lab.models import Feedback',
    `assert ${query}.count() == 1`,
    `assert ${query}.get().employee_name == ${JSON.stringify(employeeName)}`,
    `assert ${query}.get().employee.full_name == ${JSON.stringify(employeeName)}`,
  ].join('\n')]);
}

async function checkFeedback(page, employeeName, size = 'desktop') {
  await page.goto(base + '/', {waitUntil: 'networkidle'});
  const link = page.locator('.topbar').getByRole('link', {
    name: 'Обратная связь', exact: true,
  });
  assert.equal(await link.getAttribute('href'), '/feedback/');
  await Promise.all([page.waitForNavigation(), link.click()]);
  const field = page.getByLabel('Сообщение');
  assert.equal(await field.getAttribute('maxlength'), '5000');
  await field.fill('   ');
  await Promise.all([page.waitForNavigation(),
    page.getByRole('button', {name: 'Отправить', exact: true}).click()]);
  assert.equal(new URL(page.url()).pathname, '/feedback/');
  assert.equal(await field.getAttribute('aria-invalid'), 'true');
  const text = `QA обратная связь: ${employeeName}, ${size}`;
  await field.fill(text);
  await page.screenshot({path: join(artifacts, `feedback-${size}.png`),
    fullPage: true});
  await Promise.all([page.waitForNavigation(),
    page.getByRole('button', {name: 'Отправить', exact: true}).click()]);
  assert.equal(new URL(page.url()).pathname, '/');
  assert.match(await page.locator('.messages').innerText(),
    /Спасибо! Обратная связь отправлена\./);
  await page.reload({waitUntil: 'networkidle'});
  assertFeedbackSaved(text, employeeName);
}

async function checkMobileFeedback(page, employeeName, width) {
  await page.setViewportSize({width, height: 844});
  await page.goto(base + '/', {waitUntil: 'networkidle'});
  const link = page.locator('.feedback-link');
  const bounds = await link.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width,
    `Feedback button fits ${width}px viewport`);
  await checkFeedback(page, employeeName, String(width));
  assert.equal(await page.evaluate(() => (
    document.documentElement.scrollWidth > innerWidth
  )), false, `Feedback submission: ${width}px overflow`);
}

try {
  const formsOnly = process.argv.includes('--forms-only');
  const unknown = process.argv.slice(2).filter(arg => !['--headed', '--forms-only', '--firefox'].includes(arg));
  assert.equal(unknown.length, 0, `Unknown argument: ${unknown.join(' ')}`);
  let chromium;
  try {
    const playwright = require(process.env.KUDO_PLAYWRIGHT_MODULE || 'playwright');
    chromium = firefox ? playwright.firefox : playwright.chromium;
  }
  catch(error) { throw new Error(`Playwright is unavailable. Run npm ci and npm run install:browsers. ${error.message}`); }
  const requestedPort = process.env.LIMS_BROWSER_PORT;
  assert.ok(requestedPort === undefined || /^\d+$/.test(requestedPort), 'LIMS_BROWSER_PORT must be an integer from 1 to 65535.');
  const port = requestedPort === undefined ? 0 : Number(requestedPort);
  assert.ok(requestedPort === undefined || (port >= 1 && port <= 65535), 'LIMS_BROWSER_PORT must be an integer from 1 to 65535.');
  const artifactsRoot = resolve(process.env.LIMS_ARTIFACT_DIR || join(root, 'var', 'browser-artifacts'));
  mkdirSync(artifactsRoot, {recursive:true});
  artifacts = mkdtempSync(join(artifactsRoot, 'run-'));
  init(['migrate', '--noinput']);
  init(['seed_initial']);
  await startServer(port);
  browser = await chromium.launch({headless:!headed,args:firefox ? [] : ['--no-sandbox','--disable-dev-shm-usage']});
  if (interrupted || cleanupPromise) {
    await browser.close();
    throw new Error('Local browser checks were stopped.');
  }
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  // Independent UI scenarios start clean; draft persistence uses its own tab.
  await page.addInitScript(() => {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith('lims.form-draft.')) sessionStorage.removeItem(key);
    }
  });
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  const go = async path => {
    const response = await page.goto(base + path, {waitUntil:'networkidle'});
    assert.equal(response.status(),200,path);
    assert.equal(await page.locator('main h1').count(),1,path);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${path}: desktop overflow`);
  };
  const submit = async label => {
    await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:label,exact:true}).click()]);
    assert.equal(await page.locator('.errorlist,.field-error').count(),0,(await page.locator('.errorlist,.field-error').allTextContents()).join('; '));
  };
  await page.goto(base+'/login/');
  await page.locator('#id_password').fill('incorrect-demo-password');
  await page.getByRole('button',{name:/Продолжить/}).click();
  assert.match(await page.locator('.field-error').innerText(),/Неверный пароль/);
  assert.equal(await page.locator('#id_password').getAttribute('aria-invalid'),'true');
  const passwordDescription = await page.locator('#id_password').getAttribute('aria-describedby');
  assert.ok(passwordDescription?.includes('id_password_error'));
  assert.match(await page.locator('#id_password_error').innerText(),/Неверный пароль/);
  await page.locator('#id_password').fill('lims-demo');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:/Продолжить/}).click()]);
  await option(page,'#id_employee','Гущин Даниил');
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:/Открыть лабораторию/}).click()]);
  checks: {
  await checkFormDrafts(context);
  if (formsOnly) { console.log('PASS: form draft persistence checks.'); break checks; }
  await go('/');
  await checkFeedback(page, 'Гущин Даниил');
  await checkSidebarFirstPaint(browser, await context.storageState());
  assert.equal(await page.locator('.demo-banner,.demo-label').count(),0);
  assert.doesNotMatch(await page.locator('main').innerText(),/DEMO-/);
  await page.screenshot({path:join(artifacts, 'overview.png'),fullPage:true});
  const desktopPaths = ['/recipes/','/experiments/','/history/','/sources/','/materials/','/material-categories/','/material-categories/new/','/lots/','/metrics/','/mixtures/','/measurement-groups/','/measurement-groups/new/',
    '/recipe-templates/','/sources/new/','/materials/new/','/lots/new/','/metrics/new/',
    '/mixtures/new/','/recipe-templates/new/','/recipes/new/','/experiments/new/',
    '/recipes/1/','/experiments/1/','/sources/1/','/sources/4/','/feedback/'];
  for (const path of desktopPaths) {
    await go(path);
    await assertLiveSearchMarkers(page);
  }
  for (const [path, filename] of [['/lots/','lots.png'],['/material-categories/','categories.png'],['/recipe-templates/','recipe-templates.png'],
    ['/sources/1/','source.png']]) {
    await go(path); await page.screenshot({path:join(artifacts,filename),fullPage:true});
  }
  await go('/recipes/1/');
  await page.screenshot({path:join(artifacts, 'recipe.png'),fullPage:true});
  await go('/experiments/1/');
  await page.screenshot({path:join(artifacts, 'test.png'),fullPage:true});
  console.log('Desktop pages render without overflow; pristine winter UI screenshots saved.');

  await go('/material-categories/');
  assert.ok(await page.locator('tbody a[href^="/material-categories/"]').count() > 0,
    'Seeded categories must be populated');
  assert.ok((await page.locator('main').innerText()).includes('Полиолы'));
  assert.ok((await page.locator('main').innerText()).includes('Изоцианаты'));
  assert.ok((await page.locator('tbody tr td:last-child').allTextContents())
    .every(value => Number(value.trim()) > 0), 'Seeded categories have materials');
  assert.equal(await page.locator('.sidebar a[href="/material-categories/"]').count(), 1);

  await go('/recipes/?q=316.26');
  const archiveRecipe = await page.getByRole('link', {
    name: '316.26', exact: true,
  }).getAttribute('href');
  await go(archiveRecipe);
  const archiveComponent = page.locator('.panel').filter({
    has: page.getByRole('heading', {name: 'Состав · группа Смесь A'}),
  }).locator('tbody tr').first();
  assert.equal(await archiveComponent.locator('a[href^="/materials/"]').innerText(), 'Inovol C210');
  const importedLot = archiveComponent.locator('a[href^="/lots/"]');
  assert.equal(await importedLot.innerText(), '260613G');
  const importedLotPath = await importedLot.getAttribute('href');
  const archiveSource = await page.locator('.detail-grid > aside a[href^="/sources/"]').first().getAttribute('href');
  assert.match(await page.locator('#recipe-calculation').innerText(), /€/);
  await go(importedLotPath);
  assert.match(await page.locator('main').innerText(), /Inovol C210/);
  assert.match(await page.locator('main').innerText(), /€/);
  await go(archiveSource);
  await checkArchiveDownload(page);
  await go('/materials/?q=Inovol%20C210');
  assert.equal(await page.locator('tbody tr').count(), 1);
  assert.match(await page.locator('tbody tr td').nth(1).innerText(), /Полиол/i);
  console.log('Imported 316.26 uses canonical material, extracted lot, EUR prices and a verified Excel download.');
  await checkCarriedForwardFork(page, go, submit);
  await checkSelectSearch(page, go);
  await checkFormsetRemoval(page, go, submit);
  await checkRegistrySearch(page, go);
  await checkLiveMaterials(page, go);
  await checkLiveRecipes(page, go);
  await checkDirtyLivePagination(page, go);
  await checkStaleLiveResponse(page);
  await checkPendingPageRestore(page);
  await checkRecipeTemplatePicker(page, go);
  await checkPickerCSSFallback(browser, await context.storageState());

  const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,storageState:await context.storageState()});
  const mobilePage=await mobile.newPage();
  mobilePage.on('pageerror',error=>errors.push(error.message));
  const mobilePaths=['/', ...desktopPaths];
  const overflows=[];
  for(const path of mobilePaths){
    const response=await mobilePage.goto(base+path,{waitUntil:'networkidle'});
    assert.equal(response.status(),200,path);
    if(await mobilePage.evaluate(()=>document.documentElement.scrollWidth>innerWidth)){
      overflows.push(path);
      console.log('Mobile overflow:',path,await mobilePage.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,elements:[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+1;}).slice(0,10).map(el=>({tag:el.tagName,class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))})));
    }
  }
  await mobilePage.goto(base+'/',{waitUntil:'networkidle'});
  await mobilePage.screenshot({path:join(artifacts, 'mobile.png'),fullPage:true});
  console.log('Mobile route overflow findings:',JSON.stringify(overflows));
  const mobileMenu = mobilePage.locator('.mobile-menu');
  await mobileMenu.click();
  assert.equal(await mobileMenu.getAttribute('aria-expanded'),'true');
  assert.equal(await mobilePage.locator('.nav-link.active').evaluate(el => el === document.activeElement),true);
  await mobilePage.keyboard.press('Escape');
  assert.equal(await mobileMenu.getAttribute('aria-expanded'),'false');
  assert.equal(await mobileMenu.evaluate(el => el === document.activeElement),true);
  await mobileMenu.click();
  await mobilePage.mouse.click(380,100);
  assert.equal(await mobileMenu.getAttribute('aria-expanded'),'false');
  await mobileMenu.click();
  await mobilePage.locator('.logout-form button').focus();
  await mobilePage.keyboard.press('Tab');
  await mobilePage.keyboard.press('Tab');
  assert.equal(await mobileMenu.getAttribute('aria-expanded'),'false');
  await mobilePage.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await mobilePage.locator('.sidebar').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
  await mobilePage.emulateMedia({reducedMotion:'no-preference'});
  for (const viewport of [{width:768,height:1024},{width:320,height:740}]) {
    await mobilePage.setViewportSize(viewport);
    for (const path of ['/', '/recipes/new/', '/recipes/1/', '/sources/1/',
      '/feedback/']) {
      await mobilePage.goto(base+path,{waitUntil:'networkidle'});
      assert.equal(await mobilePage.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${path}: ${viewport.width}px overflow`);
      if (path === '/recipes/new/') {
        await checkMobileSelect(mobilePage, viewport.width);
      }
    }
    if (viewport.width === 768) {
      assert.equal(await mobilePage.locator('body').getAttribute('data-density'),'touch');
      assert.ok(await mobileMenu.evaluate(el=>el.getBoundingClientRect().height>=52));
      await mobilePage.goto(base+'/',{waitUntil:'networkidle'});
      await mobilePage.screenshot({path:join(artifacts,'tablet.png'),fullPage:true});
    }
  }
  for (const width of [390, 320]) {
    await checkMobileFeedback(mobilePage, 'Гущин Даниил', width);
  }
  await mobilePage.close();
  await mobile.close();
  const checkAdditionalMobile = async paths => {
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true,storageState:await context.storageState()});
    const page = await mobile.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    for (const path of paths) {
      const response = await page.goto(base+path,{waitUntil:'networkidle'});
      assert.equal(response.status(),200,path);
      if (await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) overflows.push(path);
    }
    await mobile.close();
  };

  await go('/recipes/new/');
  await page.locator('[data-add-form="components-formset"]').click();
  await page.locator('[data-add-form="components-formset"]').click();
  assert.equal(await page.locator('#id_components-TOTAL_FORMS').inputValue(),'3');
  const addedTrigger = searchableSelect(page, '#id_components-2-material')
    .locator('.search-select-trigger');
  const triggerId = await addedTrigger.getAttribute('id');
  assert.equal(await page.locator(`label[for="${triggerId}"]`).count(), 1);
  assert.equal(await page.locator('#components-formset .formset-rows').evaluate(el=>el.innerHTML.includes('__prefix__')),false);
  await option(page,'#id_mixture_type','Зимняя пена');
  await page.locator('#id_version_label').fill('QA-316.0');
  await page.locator('#id_title').fill('QA нулевой компонент и полный состав');
  await page.locator('#id_status').selectOption('published');
  const materials = await page.locator('#id_components-0-material option').evaluateAll(items => items.filter(item=>item.value).slice(0,3).map(item=>item.value));
  assert.equal(materials.length,3,'Imported material catalogue has at least three entries');
  await page.locator('#id_components-0-material').selectOption(materials[0]);
  await page.locator('#id_components-0-quantity_value').fill('0');
  await page.locator('#id_components-1-material').selectOption(materials[1]);
  await page.locator('#id_components-1-quantity_value').fill('100');
  await page.locator('#id_components-2-material').selectOption(materials[2]);
  await page.locator('#id_filling-0-group_label').fill('A');
  await page.locator('#id_filling-0-mass_g').fill('0');
  await submit('Сохранить рецептуру');
  const original = new URL(page.url()).pathname;
  assert.match(original,/^\/recipes\/\d+\/$/);
  assert.equal(await page.locator('h1').innerText(),'QA-316.0');
  assert.match(await page.locator('.panel').filter({has:page.getByRole('heading',{name:'Состав · группа A'})}).locator('tbody tr').first().locator('td').last().innerText(),/^0(?:[,.]0+)?$/);
  assert.equal(await page.locator('.panel').filter({has:page.getByRole('heading',{name:'Состав · группа A'})}).locator('tbody tr').last().locator('td').last().innerText(),'—');
  assert.equal((await page.request.get(base+original+'edit/')).status(),403);
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('h1').innerText(),'QA-316.0');

  await go(original+'fork/');
  await page.locator('#id_version_label').fill('QA-316.1');
  await page.locator('#id_title').fill('QA новая версия');
  await page.locator('#id_status').selectOption('draft');
  await submit('Сохранить рецептуру');
  const fork = new URL(page.url()).pathname;
  await go(fork+'edit/');
  await page.locator('#id_title').fill('QA опубликованная новая версия');
  await submit('Сохранить изменения');
  await changeStatus(page, submit, 'published');
  assert.equal((await page.request.get(base+fork+'edit/')).status(),403);
  await go(original);
  assert.match(await page.locator('.page-heading').innerText(),/QA нулевой компонент/);

  // QA metrics are entered through the catalogue without assuming archive labels.
  for (const [code,label,unit] of [
    ['QA_TEMP','QA температура эксперимента','°C'],
    ['QA_RH','QA относительная влажность','%'],
    ['QA_YIELD','QA выход пены','л'],
  ]) {
    await go('/metrics/new/');
    await page.locator('#id_code').fill(code);
    await page.locator('#id_label').fill(label);
    await page.locator('#id_unit_code').fill(unit);
    await page.locator('#id_value_kind').selectOption('numeric');
    await submit('Сохранить');
  }
  await go('/measurement-groups/new/');
  await page.locator('#id_name').fill('QA основные параметры');
  for (const name of ['QA температура эксперимента', 'QA относительная влажность', 'QA выход пены']) {
    await page.getByRole('checkbox', {name: new RegExp(name)}).check();
  }
  await submit('Сохранить');
  const groupIndicators = page.locator('section.panel').filter({
    has: page.getByRole('heading', {name: 'Показатели группы', exact: true}),
  });
  assert.equal(await groupIndicators.locator('tbody tr').count(), 3);
  await go('/experiments/new/?recipe='+fork.split('/')[2]);
  for (const name of ['identifier', 'source_sheet', 'match_reason']) {
    assert.equal(await page.locator('#id_'+name).count(), 0);
  }
  assert.equal(await page.locator('#conditions-formset').count(), 0);
  assert.equal(await page.locator('#id_recipe').getAttribute('required'), '');
  const linkedRecipe = await page.locator('#id_recipe option:checked').textContent();
  await page.locator('#id_title').fill('QA нулевое измерение и история');
  await page.locator('#id_status').selectOption('active');
  await page.locator('#id_recorded_tested_on').fill('01.11.2026');
  await submit('Сохранить эксперимент');
  const card=new URL(page.url()).pathname;
  assert.match(card,/^\/experiments\/\d+\/$/);
  assert.ok(linkedRecipe.includes((await page.locator('h1').innerText()).trim()));
  await checkAdditionalMobile(['measurement-groups/new/','observations/new/','conditioning/new/'].map(path=>card+path));
  await go(card+'measurement-groups/new/');
  await option(page, '#id_measurement_group', 'QA основные параметры');
  await submit('Продолжить');
  assert.equal(await page.locator('#id_source_cell').getAttribute('type'), 'hidden');
  await page.getByLabel('QA температура эксперимента, °C', {exact:true}).fill('0');
  await page.getByLabel('QA относительная влажность, %', {exact:true}).fill('55');
  await page.getByLabel('QA выход пены, л', {exact:true}).fill('0');
  await submit('Сохранить группу измерений');
  const resultPanel=page.locator('section.panel').filter({has:page.getByRole('heading',{name:'QA основные параметры',exact:true})});
  const resultRow=resultPanel.locator('tbody tr').filter({hasText:'QA выход пены'});
  assert.match(await resultRow.locator('td').nth(1).innerText(),/^0(?:[,.]0+)?$/);
  await go(card+'observations/new/');
  assert.equal(await page.locator('#id_source_cell').getAttribute('type'), 'hidden');
  await option(page,'#id_run','QA основные параметры');
  const verbatim='  Первая строка наблюдения\nВторая строка: без изменений  ';
  await page.locator('#id_text_value').fill(verbatim);
  await submit('Сохранить');
  assert.equal(await page.locator('.observation p').textContent(),verbatim);
  await go(card+'conditioning/new/');
  assert.equal(await page.locator('#id_source_cell').getAttribute('type'), 'hidden');
  await page.locator('#id_target_kind').selectOption('can');
  await page.locator('#id_kind').selectOption('conditioning');
  await page.locator('#id_duration_value').fill('0');
  await page.locator('#id_duration_unit').fill('ч');
  await page.locator('#id_temperature_c').fill('0');
  await submit('Сохранить');
  await go(card+'edit/');
  assert.equal(await page.locator('#conditions-formset').count(), 0);
  await page.locator('#id_notes').fill('QA карточка завершена после нулевого измерения');
  await submit('Сохранить эксперимент');
  await go(card+'edit/');
  assert.equal(await page.locator('#conditions-formset').count(), 0);
  await submit('Сохранить эксперимент');
  await changeStatus(page, submit, 'completed');
  assert.equal(await page.getByRole('link',{name:'Добавить группу измерений',exact:true}).count(),0);
  assert.equal((await page.request.get(base+card+'measurement-groups/new/')).status(),403);
  assert.equal((await page.request.get(base+card+'edit/')).status(),403);
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('.observation p').textContent(),verbatim);
  assert.match(await page.locator('main').innerText(),/QA карточка завершена/);
  await page.locator('details.event-details').first().locator('summary').click();
  assert.doesNotMatch(await page.locator('details.event-details').first().innerText(),/numeric_value|recorded_tested_on|recipe_matches/);
  await go('/history/?q=QA-316.1');
  assert.match(await page.locator('main').innerText(),/QA-316.1/);

  // Add documentary evidence through the same author-facing screens as a user.
  await go('/sources/new/');
  await page.locator('#id_title').fill('QA источник зимней пены');
  await page.locator('#id_kind').selectOption('note');
  await page.locator('#id_notes').fill('QA исходное описание\nВторая строка источника');
  await submit('Сохранить');
  const noteSource = new URL(page.url()).pathname;
  await go(noteSource+'sheets/new/');
  await page.locator('#id_name').fill('QA свойства сырья');
  await submit('Сохранить');
  const cellLink = await page.locator('a[href$="/cells/new/"]').first().getAttribute('href');
  await go(cellLink);
  await page.locator('#id_address').fill('B12');
  await page.locator('#id_cell_kind').selectOption('numeric');
  await page.locator('#id_raw_value').fill('  1,05  ');
  await submit('Сохранить');
  assert.ok((await page.locator('main').innerText()).includes('B12'));
  assert.equal(await page.locator('tbody tr').filter({hasText:'B12'}).locator('.preserve-text').first().textContent(),'  1,05  ');
  await go('/sources/new/');
  await page.locator('#id_title').fill('QA ссылка на протокол');
  await page.locator('#id_kind').selectOption('link');
  await page.locator('#id_url').fill('https://example.com/qa-winter-protocol');
  await submit('Сохранить');
  assert.equal(await page.locator('a[target="_blank"]').first().getAttribute('href'),'https://example.com/qa-winter-protocol');
  await go('/sources/new/');
  await page.locator('#id_title').fill('QA паспорт партии');
  await page.locator('#id_kind').selectOption('file');
  const sourceBytes=Buffer.from('QA паспорт зимней партии\nБез импорта таблиц.','utf8');
  const digest=createHash('sha256').update(sourceBytes).digest('hex');
  await page.locator('#id_upload').setInputFiles({name:'qa-winter-passport.txt',mimeType:'text/plain',buffer:sourceBytes});
  await submit('Сохранить');
  const uploadedSource=new URL(page.url()).pathname;
  assert.ok((await page.locator('main').innerText()).includes(digest));
  const download=await page.request.get(base+uploadedSource+'download/');
  assert.equal(download.status(),200);
  assert.equal(createHash('sha256').update(await download.body()).digest('hex'),digest);
  assert.match(download.headers()['content-disposition'],/attachment/);

  // A real lot can be registered, quarantined, released and traced to a plan.
  await go('/material-categories/new/');
  await page.locator('#id_name').fill('QA полиолы');
  await page.locator('#id_description').fill('QA описание категории сырья');
  await submit('Сохранить');
  const categoryPath = new URL(page.url()).pathname;
  await go('/materials/new/');
  await page.locator('#id_name').fill('QA зимний полиол');
  await page.locator('#id_category').selectOption('QA полиолы');
  await page.locator('#id_supplier').fill('QA поставщик');
  assert.equal(await page.locator('#id_density_g_ml,#id_price_per_kg,#id_price_currency').count(), 0);
  await submit('Сохранить');
  const materialPath=new URL(page.url()).pathname;
  const materialId=materialPath.split('/')[2];
  await go(categoryPath + 'edit/');
  await page.locator('#id_name').fill('QA полиолы уточнённые');
  await submit('Сохранить');
  assert.equal(await page.locator(`a[href="${materialPath}"]`).count(), 1);
  await go(materialPath);
  assert.match(await page.locator('main').innerText(), /QA полиолы уточнённые/);
  await go('/materials/?category=' + encodeURIComponent('QA полиолы уточнённые'));
  assert.equal(await page.locator('tbody tr').count(), 1);
  await go('/material-categories/');
  assert.match(await page.locator('main').innerText(), /QA описание категории сырья/);
  await go('/lots/new/?material='+materialId);
  await page.locator('#id_lot_code').fill('QA-WINTER-LOT-001');
  await page.locator('#id_status').selectOption('quarantine');
  await page.locator('#id_density_g_ml').fill('1.1');
  await page.locator('#id_price_per_kg').fill('200');
  await page.locator('#id_price_currency').selectOption('RUB');
  await page.locator('#id_source').selectOption(uploadedSource.split('/')[2]);
  await submit('Сохранить');
  const lotPath=new URL(page.url()).pathname;
  const lotId=lotPath.split('/')[2];
  assert.ok((await page.locator('main').innerText()).includes('Карантин'));
  assert.equal(await page.locator(`a[href="${uploadedSource}"]`).count(),1);

  // Fixed template versions can be composed and instantiated through forms.
  await go('/recipe-templates/new/');
  await page.locator('#id_name').fill('QA шаблон зимней пены');
  await option(page,'#id_mixture_type','Зимняя пена');
  assert.equal(await page.locator('#id_calculation_template').count(), 0);
  await page.locator('#id_batch_mass_g').fill('1000');
  await page.locator('#id_components-0-specified_lot').selectOption(lotId);
  await page.locator('#id_components-0-quantity_value').fill('100');
  await page.locator('#id_components-0-completeness').selectOption('complete');
  await submit('Сохранить шаблон рецептуры');
  const recipeTemplatePath=new URL(page.url()).pathname;
  const recipeTemplateId=recipeTemplatePath.split('/')[2];
  await go(recipeTemplatePath+'fork/');
  assert.equal(await page.locator('#id_version').inputValue(),'2');
  await page.locator('#id_description').fill('QA вторая версия шаблона рецептуры');
  await submit('Сохранить шаблон рецептуры');
  assert.notEqual(new URL(page.url()).pathname,recipeTemplatePath);
  await go('/recipes/new/?template='+recipeTemplateId);
  assert.equal(await page.locator('#id_components-0-specified_lot').inputValue(),lotId);
  assert.equal(await page.locator('#id_components-0-quantity_value').inputValue(),'100');
  await page.locator('#id_version_label').fill('QA-WINTER-CALC.1');
  await page.locator('#id_title').fill('QA зимняя пена с расчётом');
  await page.locator('#id_status').selectOption('published');
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:'Сохранить рецептуру',exact:true}).click()]);
  assert.match((await page.locator('.errorlist,.field-error').allTextContents()).join(' '),/Партия.*недоступна/);
  await page.locator('#id_status').selectOption('draft');
  await submit('Сохранить рецептуру');
  const calculatedRecipePath=new URL(page.url()).pathname;
  await go(lotPath);
  await changeStatus(page, submit, 'active');
  await go(calculatedRecipePath+'edit/');
  await page.locator('#id_status').selectOption('published');
  await submit('Сохранить изменения');
  const cost=async()=>page.locator('.calculation-group').first().locator('.result-item').filter({hasText:'Стоимость группы'}).locator('strong').innerText();
  assert.match(await cost(),/^200\s*₽$/);
  const baselineCost=await cost();
  const baselineCalculation=await page.locator('a[href^="/calculations/"]').first().getAttribute('href');
  assert.ok(baselineCalculation);
  await go(lotPath);
  assert.equal(await page.locator(`a[href="${calculatedRecipePath}"]`).count(),1);
  await go(lotPath+'edit/');
  await page.locator('#id_price_per_kg').fill('400');
  await submit('Сохранить');
  await go(calculatedRecipePath);
  assert.equal(await cost(),baselineCost,'published baseline calculation changed after catalogue edit');
  await go(baselineCalculation);
  assert.equal(await cost(),baselineCost,'saved record changed after catalogue edit');
  await go(calculatedRecipePath);
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.locator(`form[action="${calculatedRecipePath}calculate/"] button`).click()]);
  assert.equal(await cost(),baselineCost,'explicit recalculation replaced original baseline');
  const calculationLinks=await page.locator('a[href^="/calculations/"]').evaluateAll(items=>[...new Set(items.map(item=>new URL(item.href).pathname))]);
  const newCalculation=calculationLinks.find(path=>path!==baselineCalculation);
  assert.ok(newCalculation,'new recalculation record not linked');
  await go(newCalculation);
  assert.match(await cost(),/^400\s*₽$/);
  await go(baselineCalculation);
  assert.equal(await cost(),baselineCost);
  await checkAdditionalMobile([categoryPath,categoryPath+'edit/',lotPath,recipeTemplatePath,baselineCalculation]);
  for (const [path,filename] of [[baselineCalculation,'calculation.png']]) {
    await go(path);
    await page.screenshot({path:join(artifacts,filename),fullPage:true});
  }
  await checkLiveTemplatesAndHistory(page, go);
  await go('/history/?entity=source&q=QA');
  const historyTarget=page.locator('.event-meta a').first();
  assert.ok((await historyTarget.getAttribute('href')).startsWith('/sources/'));
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),historyTarget.click()]);
  assert.ok(new URL(page.url()).pathname.startsWith('/sources/'));
  console.log('Sources, uploads/SHA, provenance, material categories/rename/filter, materials/lots, quarantine, recipe templates and automatic calculations and immutable recalculation all passed.');

  await go('/employees/');
  await option(page,'#id_employee','Иван Иванов');
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:/Открыть лабораторию/}).click()]);
  await checkFeedback(page, 'Иван Иванов');
  await go('/recipes/');
  assert.equal(await page.locator('a[href="/recipes/new/"]').count(),0);
  await go(card);
  for(const word of ['Редактировать','Изменить статус','Добавить группу измерений','Добавить наблюдение','Записать обработку']) assert.equal(await page.getByRole('link',{name:new RegExp(word)}).count(),0,`Reader controls: ${word}`);
  const csrf=await page.locator('[name="csrfmiddlewaretoken"]').first().inputValue();
  for(const path of ['/recipes/new/','/experiments/new/',card+'measurement-groups/new/',card+'observations/new/',card+'conditioning/new/',
    '/sources/new/','/materials/new/','/material-categories/new/',categoryPath+'edit/','/lots/new/','/metrics/new/','/mixtures/new/','/recipe-templates/new/','/measurement-groups/new/',
    calculatedRecipePath+'calculate/',uploadedSource+'sheets/new/',
    card+'status/',fork+'status/',lotPath+'status/']){
    const response=await page.request.post(base+path,{form:{csrfmiddlewaretoken:csrf},headers:{Referer:base+card}});
    assert.equal(response.status(),403,`Reader direct POST ${path}`);
    assert.match(await response.text(),/читатель/);
  }
  await go(uploadedSource);
  assert.equal(await page.locator(`a[href="${uploadedSource}edit/"]`).count(),0);
  assert.equal((await page.request.get(base+uploadedSource+'download/')).status(),200);
  await page.reload({waitUntil:'networkidle'});
  assert.match(await page.locator('.profile').innerText(),/Иван Иванов/);
  assert.deepEqual(errors,[]);
  assert.deepEqual(overflows,[],`Mobile overflow: ${overflows.join(', ')}`);
  console.log(`PASS: isolated real initial dataset; login, author/reader roles, direct POST denial, desktop/mobile routes, documentary sources and lots, fixed recipe templates and automatic calculations, immutable saved calculations, immutable recipes, fork/edit/publish, zero and missing values, card/result/observation/treatment creation, completion locking, reload persistence, audit search. Screenshots: ${artifacts}`);
  }
} catch(error) {
  if (!interrupted) {
    console.error(error);
    if(logs.includes('Traceback')) console.error(logs.slice(-10000));
    process.exitCode = 1;
  }
} finally {
  await cleanup();
  for (const [signal, handler] of signalHandlers) process.off(signal, handler);
}
