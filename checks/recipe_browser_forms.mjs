import assert from 'node:assert/strict';

function rowControls(page) {
  const rows = prefix => page.locator(`#${prefix}-formset .formset-rows > .formset-row`);
  const add = prefix => page.locator(`[data-add-form="${prefix}-formset"]`).click();
  const remove = (prefix, index) => rows(prefix).nth(index).locator('[data-remove-form]').click();
  const counts = async (prefix, count, initial) => {
    assert.equal(await rows(prefix).count(), count, prefix);
    assert.equal(await page.locator(`#id_${prefix}-TOTAL_FORMS`).inputValue(), String(count));
    assert.equal(await page.locator(`#id_${prefix}-INITIAL_FORMS`).inputValue(), String(initial));
  };
  const clear = async prefix => {
    while (await rows(prefix).count()) await remove(prefix, 0);
    await counts(prefix, 0, 0);
    assert.equal(await page.locator(`[data-add-form="${prefix}-formset"]`)
      .evaluate(button => button === document.activeElement), true);
  };
  return {rows, add, remove, counts, clear};
}

async function firstMaterial(page, prefix = 'polyol') {
  const material = await page.locator(`#id_${prefix}-0-material option`)
    .evaluateAll(options => options.find(option => option.value)?.value);
  assert.ok(material);
  return material;
}

async function submitInvalid(page, label) {
  await Promise.all([
    page.waitForNavigation({waitUntil: 'networkidle'}),
    page.getByRole('button', {name: label, exact: true}).click(),
  ]);
}

async function prepareRemoval(page, controls, completeRecipeInputs) {
  const prefixes = ['polyol', 'gases'];
  for (const prefix of prefixes) {
    await controls.counts(prefix, 1, 0);
    await controls.add(prefix);
    await controls.add(prefix);
  }
  assert.equal(await page.locator('[name$="-DELETE"], .formset-source').count(), 0);
  const material = await firstMaterial(page);
  await page.locator('#id_version_label').fill('QA-FORMSET-REMOVE');
  for (let index = 0; index < 3; index++) {
    for (const prefix of prefixes) {
      await page.locator(`#id_${prefix}-${index}-material`).selectOption(material);
      await page.locator(`#id_${prefix}-${index}-quantity`).fill(index === 1 ? '-1' : '50');
    }
  }
  for (const prefix of prefixes) {
    await controls.remove(prefix, 1);
    await controls.counts(prefix, 2, 0);
  }
  await completeRecipeInputs(page);
  return material;
}

async function retryRemoval(page, controls, searchableSelect, material) {
  assert.equal(await page.locator('#id_polyol-1-quantity').inputValue(), '50');
  const selected = searchableSelect(page, '#id_polyol-1-material');
  await selected.locator('.search-select-trigger').click();
  await selected.locator('.search-select-input').press('Escape');
  assert.equal(await selected.locator('select').inputValue(), material);
  await page.locator('#id_settings-fill_volume_ml').fill('');
  await page.locator('#id_settings-fill_volume_ml_reason').fill('');
  await submitInvalid(page, 'Сохранить рецептуру');
  assert.match((await page.locator('.field-error').allTextContents()).join(' '),
    /Укажите значение или причину/);
  for (const prefix of ['polyol', 'gases']) await controls.counts(prefix, 2, 0);
  await page.locator('#id_settings-fill_volume_ml').fill('800');
}

async function checkSavedRemoval(page, tools, controls, material) {
  const {go, submit, completeRecipeInputs} = tools;
  const recipe = new URL(page.url()).pathname;
  await go(recipe + 'edit/');
  const stable = await page.locator('#id_polyol-1-row_id').inputValue();
  assert.ok(stable);
  for (const prefix of ['polyol', 'gases']) {
    await controls.counts(prefix, 3, 2);
    await controls.remove(prefix, 0);
    await controls.remove(prefix, 1);
    await controls.counts(prefix, 1, 1);
  }
  await page.locator('#id_polyol-0-quantity').fill('100');
  await completeRecipeInputs(page);
  await submit('Сохранить изменения');
  await go(recipe + 'edit/');
  for (const prefix of ['polyol', 'gases']) await controls.counts(prefix, 2, 1);
  assert.equal(await page.locator('#id_polyol-0-row_id').inputValue(), stable);
  assert.equal(await page.locator('#id_polyol-0-quantity').inputValue(), '100');
  for (const prefix of ['polyol', 'gases']) await controls.clear(prefix);
  await submitInvalid(page, 'Сохранить изменения');
  assert.match(await page.locator('main').innerText(), /Добавьте компоненты/);
  await controls.add('polyol');
  await page.locator('#id_polyol-0-material').selectOption(material);
  await page.locator('#id_polyol-0-quantity').fill('100');
  await completeRecipeInputs(page);
  await submit('Сохранить изменения');
}

export async function checkModernRecipeRemoval(page, tools) {
  const controls = rowControls(page);
  await tools.go('/recipes/new/');
  const material = await prepareRemoval(page, controls, tools.completeRecipeInputs);
  await retryRemoval(page, controls, tools.searchableSelect, material);
  await tools.submit('Сохранить рецептуру');
  await checkSavedRemoval(page, tools, controls, material);
  console.log('Formsets: modern recipe deletion, stable IDs, reindexing and validation retry passed.');
}

async function snapshot(page) {
  return page.evaluate(() => {
    const form = document.querySelector('[data-formset-prefix], form[data-form-draft]').closest('form');
    return {
      fields: [...new FormData(form)].filter(([name]) => name !== 'csrfmiddlewaretoken'),
      dates: [...form.querySelectorAll('.date-picker input[type="text"]')].map(input => input.value),
      rows: [...form.querySelectorAll('[data-formset-prefix]')].map(group => [
        group.dataset.formsetPrefix, group.querySelectorAll('.formset-rows > .formset-row').length,
      ]),
    };
  });
}

async function settlePreview(page) {
  if (!await page.locator('[data-recipe-preview-status]').count()) return;
  await page.waitForFunction(() => (
    document.querySelector('[data-recipe-preview-status]')?.dataset.state !== 'loading'
  ));
}

async function reloadDraft(page) {
  await settlePreview(page);
  const before = await snapshot(page);
  await page.reload({waitUntil: 'networkidle'});
  await settlePreview(page);
  assert.deepEqual(await snapshot(page), before,
    'Reload preserves fields, dates, stable IDs and every row');
}

async function prepareDraft(page, controls, completeRecipeInputs) {
  await page.locator('#id_version_label').fill('QA-REFRESH-DRAFT');
  await page.locator('#id_title').fill('Черновик перед обновлением');
  await page.locator('.recipe-notes > summary').click();
  await page.locator('#id_notes').fill('Первая строка\nВторая строка');
  await page.locator('#id_recorded_on').fill('03.11.2026');
  const material = await firstMaterial(page);
  for (const prefix of ['polyol', 'gases']) {
    await controls.add(prefix);
    await controls.add(prefix);
  }
  for (let index = 0; index < 3; index++) {
    for (const prefix of ['polyol', 'gases']) {
      await page.locator(`#id_${prefix}-${index}-material`).selectOption(material);
      await page.locator(`#id_${prefix}-${index}-quantity`).fill(String([0, 10, 100][index]));
    }
  }
  await completeRecipeInputs(page);
  await reloadDraft(page);
  for (const prefix of ['polyol', 'gases']) await controls.remove(prefix, 1);
  await reloadDraft(page);
  assert.equal(await page.locator('#id_polyol-1-quantity').inputValue(), '100');
  await page.locator('#id_recorded_on').fill('03.11.20__');
  await reloadDraft(page);
  await page.locator('#id_recorded_on').fill('03.11.2026');
  return material;
}

async function checkDraftIsolation(context, page, visit, base) {
  await visit('/recipes/');
  await visit('/recipes/new/');
  assert.equal(await page.locator('#id_polyol-TOTAL_FORMS').inputValue(), '2');
  assert.equal(await page.locator('#id_title').inputValue(), 'Черновик перед обновлением');
  const independent = await context.newPage();
  await independent.goto(base + '/recipes/new/', {waitUntil: 'networkidle'});
  assert.equal(await independent.locator('#id_polyol-TOTAL_FORMS').inputValue(), '1');
  assert.equal(await independent.locator('#id_title').inputValue(), '');
  await independent.close();
}

async function saveDraftAfterRetry(page, visit, completeRecipeInputs) {
  await page.locator('#id_settings-fill_volume_ml').fill('');
  await page.locator('#id_settings-fill_volume_ml_reason').fill('');
  await submitInvalid(page, 'Сохранить рецептуру');
  assert.match((await page.locator('.field-error').allTextContents()).join(' '),
    /Укажите значение или причину/);
  await page.locator('#id_title').fill('Исправление после ошибки');
  await reloadDraft(page);
  await page.locator('#id_settings-fill_volume_ml').fill('800');
  await completeRecipeInputs(page);
  await submitInvalid(page, 'Сохранить рецептуру');
  assert.equal(await page.locator('.errorlist,.field-error').count(), 0);
  const recipe = new URL(page.url()).pathname;
  assert.match(recipe, /^\/recipes\/\d+\/$/);
  await visit('/recipes/new/');
  assert.equal(await page.locator('#id_polyol-TOTAL_FORMS').inputValue(), '1');
  assert.equal(await page.locator('#id_version_label').inputValue(), '');
  return recipe;
}

async function editDraftAndServerChange(page, visit, controls, recipe, base) {
  await visit(recipe + 'edit/');
  const stable = await page.locator('#id_polyol-1-row_id').inputValue();
  await controls.remove('polyol', 0);
  await controls.remove('gases', 0);
  await reloadDraft(page);
  await submitInvalid(page, 'Сохранить изменения');
  assert.equal(await page.locator('.field-error,.errorlist').count(), 0,
    (await page.locator('.field-error,.errorlist').allTextContents()).join('; '));
  await visit(recipe + 'edit/');
  assert.equal(await page.locator('#id_polyol-INITIAL_FORMS').inputValue(), '1');
  assert.equal(await page.locator('#id_polyol-0-quantity').inputValue(), '100');
  assert.equal(await page.locator('#id_polyol-0-row_id').inputValue(), stable);
  await page.locator('#id_title').fill('Старое локальное изменение');
  const payload = Object.fromEntries((await snapshot(page)).fields);
  payload.csrfmiddlewaretoken = await page.locator(
    'form[data-recipe-editor] [name="csrfmiddlewaretoken"]').inputValue();
  payload.title = 'Сохранено другим запросом';
  const response = await page.request.post(base + recipe + 'edit/', {
    form: payload, headers: {Referer: page.url()},
  });
  assert.equal(response.status(), 200);
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(await page.locator('#id_title').inputValue(), payload.title);
}

async function cancelOtherDrafts(page, visit) {
  await visit('/experiments/new/');
  await page.locator('#id_title').fill('Черновик эксперимента');
  await page.locator('#id_recorded_tested_on').fill('04.11.2026');
  await reloadDraft(page);
  await page.locator('.form-actions a').click();
  await page.waitForLoadState('networkidle');
  await visit('/experiments/new/');
  assert.equal(await page.locator('#id_title').inputValue(), '');
}

async function experimentDraft(page, visit) {
  await visit('/experiments/new/');
  const recipe = await page.locator('#id_recipe option')
    .evaluateAll(options => options.find(option => option.value)?.value);
  await page.locator('#id_recipe').selectOption(recipe);
  await page.locator('#id_title').fill('QA Ctrl+R существующей карточки');
  await submitInvalid(page, 'Сохранить эксперимент');
  assert.equal(await page.locator('.errorlist,.field-error').count(), 0);
  const card = new URL(page.url()).pathname;
  await visit(card + 'edit/');
  await reloadDraft(page);
  await page.locator('#id_notes').fill('Введено до Ctrl+R\nНе сохранено в карточку');
  await reloadDraft(page);
  await reloadDraft(page);
  await visit(card + 'measurement-groups/new/');
  const group = await page.locator('#id_measurement_group option')
    .evaluateAll(options => options.find(option => option.value)?.value);
  await page.locator('#id_measurement_group').selectOption(group);
  await submitInvalid(page, 'Продолжить');
  const metric = page.locator('input[name^="metric_"]').first();
  await metric.fill('0');
  for (const reason of await page.locator('input[name$="_reason"]').all()) {
    const name = (await reason.getAttribute('name')).replace(/_reason$/, '');
    if (await page.locator(`[name="${name}"]`).inputValue() === '') await reason.fill('Не было оборудования');
  }
  await reloadDraft(page);
  assert.equal(await metric.inputValue(), '0');
  await submitInvalid(page, 'Сохранить группу измерений');
  assert.match(await page.locator('main').innerText(), /Группа измерений сохранена/);
}

export async function checkModernRecipeDrafts(context, tools) {
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const visit = path => page.goto(tools.base + path, {waitUntil: 'networkidle'});
  const controls = rowControls(page);
  await visit('/recipes/new/');
  const material = await prepareDraft(page, controls, tools.completeRecipeInputs);
  assert.match(await tools.searchableSelect(page, '#id_polyol-1-material')
    .locator('.search-select-trigger').innerText(), /\S/);
  await checkDraftIsolation(context, page, visit, tools.base);
  const recipe = await saveDraftAfterRetry(page, visit, tools.completeRecipeInputs);
  await editDraftAndServerChange(page, visit, controls, recipe, tools.base);
  await cancelOtherDrafts(page, visit);
  await experimentDraft(page, visit);
  await visit('/recipes/?q=302.84');
  const archive = await page.getByRole('link', {name: '302.84', exact: true}).getAttribute('href');
  await visit(archive + 'fork/');
  const stable = await page.locator('#id_polyol-1-row_id').inputValue();
  await controls.remove('polyol', 0);
  await reloadDraft(page);
  assert.equal(await page.locator('#id_polyol-0-row_id').inputValue(), stable);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Drafts: modern rows, refresh/dates, stable IDs, retries, tab isolation, server changes, success and cancel passed.');
}
