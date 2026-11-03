const MISSING_REASON = 'Значение не задано для проверки интерфейса';

async function fillEmpty(page, name, value) {
  const input = page.locator(`[name="${name}"]`);
  if (await input.count() && await input.inputValue() === '') await input.fill(value);
}

function usedRow(row) {
  return row.evaluate(element => {
    const identity = element.querySelector('input[name$="-row_id"]');
    if (identity?.value) return true;
    return [...element.querySelectorAll('input,select,textarea')].some(field => (
      !['hidden', 'checkbox'].includes(field.type)
      && !field.name.endsWith('-price_currency') && field.value.trim() !== ''
    ));
  });
}

async function completeRowReasons(row, reason) {
  if (!await usedRow(row)) return;
  await row.locator('details').first().evaluate(details => { details.open = true; });
  for (const field of await row.locator('input[name$="_reason"],textarea[name$="_reason"]').all()) {
    const name = await field.getAttribute('name');
    const valueName = name.endsWith('-lot_reason')
      ? name.replace(/-lot_reason$/, '-specified_lot') : name.replace(/_reason$/, '');
    const value = row.locator(`[name="${valueName}"]`);
    if (!await value.count() || await value.inputValue() !== '') continue;
    if (name.endsWith('-ohv_reason')) {
      const applicable = row.locator('[name$="-ohv_applicable"]');
      if (await applicable.count() && !await applicable.isChecked()) continue;
    }
    if (await field.inputValue() === '') await field.fill(reason);
  }
}

export async function completeRecipeInputs(page, options = {}) {
  if (!await page.locator('form[data-recipe-editor]').count()) return;
  const reason = options.reason || MISSING_REASON;
  const isocyanate = page.locator('#id_settings-isocyanate_present');
  if (!options.isocyanate) {
    await isocyanate.uncheck();
    await fillEmpty(page, 'settings-isocyanate_absence_reason',
      'Изоцианат не используется в этой проверке интерфейса');
  } else await isocyanate.check();
  for (const [name, value] of Object.entries({
    batch_mass_g: '1000', polyol_parts: '100',
    gas_mass_pct: options.gasMassPct ?? '0',
    fill_volume_ml: options.fillVolume ?? '800',
  })) await fillEmpty(page, `settings-${name}`, value);
  for (const row of await page.locator(
    '#polyol-formset .formset-rows > .formset-row,'
      + '#gases-formset .formset-rows > .formset-row',
  ).all()) await completeRowReasons(row, reason);
  if (options.isocyanate) {
    await completeRowReasons(page.locator('[data-recipe-row-kind="isocyanate"]'), reason);
    const mode = await page.locator('#id_settings-iso_ratio_mode').inputValue();
    const field = mode === 'index' ? 'iso_index' : 'isocyanate_parts';
    if (await page.locator(`#id_settings-${field}`).inputValue() === '') {
      await fillEmpty(page, `settings-${field}_reason`, reason);
    }
  }
}
