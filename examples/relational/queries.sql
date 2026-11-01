-- Read-only examples. Run after schema.sql and data.sql in a demonstration DB.

-- 1. Composition belongs to a group, which supplies the percentage basis.
-- Material is obtained through a lot OR an unbatched material, never both.
SELECT v.id AS recipe_id, t.code AS mixture_type, v.version_label,
       g.label AS mixture_group, g.quantity_basis, c.position,
       COALESCE(batched.name, unbatched.name) AS material,
       lot.lot_code AS specified_lot, c.quantity_value,
       sh.name AS source_sheet, name_cell.address AS name_cell,
       quantity_cell.address AS quantity_cell
FROM lims_example.recipe_component c
JOIN lims_example.recipe_group g ON g.id = c.group_id
JOIN lims_example.recipe_version v ON v.id = g.recipe_version_id
LEFT JOIN lims_example.mixture_type t ON t.id = v.mixture_type_id
LEFT JOIN lims_example.material_lot lot ON lot.id = c.specified_lot_id
LEFT JOIN lims_example.material batched ON batched.id = lot.material_id
LEFT JOIN lims_example.material unbatched ON unbatched.id = c.unbatched_material_id
LEFT JOIN lims_example.source_cell name_cell ON name_cell.id = c.name_cell_id
LEFT JOIN lims_example.source_cell quantity_cell ON quantity_cell.id = c.quantity_cell_id
LEFT JOIN lims_example.source_sheet sh ON sh.id = name_cell.sheet_id
WHERE v.id IN (1, 2) AND c.quantity_value > 0
ORDER BY v.id, c.position;

-- 2. One result remains one row; candidate recipe links are deliberately not
-- joined here because a session may have several and would multiply results.
SELECT s.id AS session_id, sh.name AS card_sheet,
       s.recorded_manufactured_on, s.recorded_tested_on,
       r.id AS result_id, m.code AS metric, r.numeric_value, m.unit_code,
       tr.method_code, c.address AS source_cell
FROM lims_example.result r
JOIN lims_example.metric m ON m.id = r.metric_id
JOIN lims_example.test_run tr ON tr.id = r.test_run_id
JOIN lims_example.test_session s ON s.id = tr.session_id
LEFT JOIN lims_example.source_sheet sh ON sh.id = s.source_sheet_id
LEFT JOIN lims_example.source_cell c ON c.id = r.source_cell_id
WHERE s.id IN (1, 2) AND m.code IN ('tack_free', 'cut_time')
ORDER BY s.id, m.code;

-- 3. Labels establish a candidate link, not a manufacturing confirmation.
SELECT rm.session_id, sh.name AS card_sheet, rm.recipe_version_id,
       rm.status, rm.reason, v.recorded_on AS recipe_sheet_date,
       s.recorded_manufactured_on, s.recorded_tested_on
FROM lims_example.recipe_match rm
JOIN lims_example.test_session s ON s.id = rm.session_id
JOIN lims_example.recipe_version v ON v.id = rm.recipe_version_id
LEFT JOIN lims_example.source_sheet sh ON sh.id = s.source_sheet_id
ORDER BY rm.session_id;

-- 4. A cached 100 remains evidence; no accepted shrinkage value is created.
SELECT sh.name AS card_sheet, c.address, c.formula_text,
       c.cached_value AS unvalidated_excel_cache,
       r.numeric_value AS extracted_or_checked_result, r.value_state
FROM lims_example.result r
JOIN lims_example.metric m ON m.id = r.metric_id
JOIN lims_example.source_cell c ON c.id = r.source_cell_id
JOIN lims_example.source_sheet sh ON sh.id = c.sheet_id
WHERE m.code = 'shrinkage'
ORDER BY sh.name;

-- 5. Accepted planned masses stay NULL while formula chains are unverified.
SELECT p.id, p.recipe_version_id, label.raw_value AS line_label,
       p.mass_g AS accepted_planned_mass_g, p.mass_status,
       mass.formula_text, mass.cached_value AS unvalidated_excel_cache
FROM lims_example.filling_plan_line p
LEFT JOIN lims_example.source_cell label ON label.id = p.label_cell_id
LEFT JOIN lims_example.source_cell mass ON mass.id = p.mass_cell_id
ORDER BY p.recipe_version_id, p.id;

-- 6. Which recorded measurements support each independently recalculated mass?
SELECT out_result.id AS calculated_result_id, out_result.numeric_value,
       dep.role, in_result.id AS input_result_id,
       in_result.numeric_value AS input_numeric_value, cell.address AS input_cell
FROM lims_example.result_dependency dep
JOIN lims_example.result out_result ON out_result.id = dep.result_id
JOIN lims_example.result in_result ON in_result.id = dep.input_result_id
LEFT JOIN lims_example.source_cell cell ON cell.id = in_result.source_cell_id
ORDER BY dep.result_id, dep.role;
