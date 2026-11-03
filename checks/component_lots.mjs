import assert from 'node:assert/strict';

function seedLots(init) {
  init(['shell', '-c', `
from lab.models import Material, MaterialLot
for suffix in ('A', 'B', 'C'):
    material = Material.objects.create(name='Проверка партий: ' + suffix)
    for number in range(1, {'A': 3, 'B': 2, 'C': 1}[suffix]):
        MaterialLot.objects.create(
            material=material, lot_code=f'FILTER-{suffix}-{number}',
        )
`]);
}

function lotSelect(page, index = 0) {
  return page.locator(`#id_components-${index}-specified_lot`);
}

function availableLots(page, index = 0) {
  return lotSelect(page, index).locator('option').evaluateAll(options => (
    options.filter(option => option.value && !option.disabled && !option.hidden)
      .map(option => option.label.split(' · ').at(-1))
  ));
}

async function selectMaterial(page, suffix, index = 0) {
  await page.locator(`#id_components-${index}-material`).selectOption(
    suffix ? {label: `Проверка партий: ${suffix}`} : '',
  );
}

async function chooseLot(page, suffix, index = 0) {
  const dropdown = lotSelect(page, index).locator('..');
  await dropdown.locator('.search-select-trigger').click();
  await dropdown.getByRole('option', {name: new RegExp(`FILTER-${suffix}$`)}).click();
}

async function checkFiltering(page) {
  assert.deepEqual(await availableLots(page), []);
  await selectMaterial(page, 'A');
  assert.deepEqual(await availableLots(page), ['FILTER-A-1', 'FILTER-A-2']);
  const dropdown = lotSelect(page).locator('..');
  await dropdown.locator('.search-select-trigger').click();
  assert.equal(await dropdown.getByRole('option').count(), 2);
  await dropdown.locator('.search-select-input').fill('FILTER-B');
  assert.equal(await dropdown.getByRole('option').count(), 0);
  await dropdown.locator('.search-select-input').press('Escape');
  await chooseLot(page, 'A-1');
  await selectMaterial(page, 'B');
  assert.equal(await lotSelect(page).inputValue(), '');
  assert.deepEqual(await availableLots(page), ['FILTER-B-1']);
  await selectMaterial(page, 'C');
  assert.deepEqual(await availableLots(page), []);
  await selectMaterial(page, '');
  assert.deepEqual(await availableLots(page), []);
}

async function checkRowsAndReload(page) {
  await selectMaterial(page, 'A');
  await chooseLot(page, 'A-2');
  await page.locator('[data-add-form="components-formset"]').click();
  assert.deepEqual(await availableLots(page, 1), []);
  await selectMaterial(page, 'B', 1);
  await chooseLot(page, 'B-1', 1);
  await page.locator('#id_components-1-quantity_value').fill('100');
  await page.reload({waitUntil: 'networkidle'});
  assert.deepEqual(await availableLots(page), ['FILTER-A-1', 'FILTER-A-2']);
  assert.deepEqual(await availableLots(page, 1), ['FILTER-B-1']);
  assert.match(await lotSelect(page).locator('option:checked').innerText(), /FILTER-A-2$/);
  assert.match(await lotSelect(page, 1).locator('option:checked').innerText(), /FILTER-B-1$/);
  await page.locator('#components-formset [data-remove-form]').first().click();
  assert.deepEqual(await availableLots(page), ['FILTER-B-1']);
  await selectMaterial(page, 'A');
  assert.equal(await lotSelect(page).inputValue(), '');
  await chooseLot(page, 'A-1');
}

async function checkSavedLot(page, base) {
  await page.locator('#id_version_label').fill('FILTER-RECIPE');
  await Promise.all([
    page.waitForNavigation({waitUntil: 'networkidle'}),
    page.getByRole('button', {name: 'Сохранить рецептуру', exact: true}).click(),
  ]);
  assert.equal(await page.locator('.errorlist,.field-error').count(), 0);
  const recipe = new URL(page.url()).pathname;
  assert.match(recipe, /^\/recipes\/\d+\/$/);
  for (const action of ['edit', 'fork']) {
    await page.goto(base + recipe + action + '/', {waitUntil: 'networkidle'});
    assert.match(await page.locator('#id_components-0-material option:checked')
      .innerText(), /Проверка партий: A/);
    assert.match(await lotSelect(page).locator('option:checked').innerText(), /FILTER-A-1$/);
    assert.deepEqual(await availableLots(page), ['FILTER-A-1', 'FILTER-A-2']);
  }
}

export async function checkComponentLots(context, base, init) {
  seedLots(init);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/recipes/new/', {waitUntil: 'networkidle'});
  await checkFiltering(page);
  await checkRowsAndReload(page);
  await checkSavedLot(page, base);
  await page.goto(base + '/recipe-templates/new/', {waitUntil: 'networkidle'});
  await checkFiltering(page);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Component lots: filtering, search, switching, dynamic rows, reload, save, edit, fork and templates passed.');
}
