-- Демонстрационный DDL утверждённого ядра пилота для PostgreSQL 18.
-- Это не миграция рабочей LIMS; ядро содержит 20 таблиц.
-- Файл сам не запускался против рабочей БД и не импортирует Excel.
-- Схема создаётся заново; существующая lims_example вызывает ошибку.
-- DROP и изменение существующих схем не выполняются.
-- Читаемые bigint ID задаются явно в демонстрационных INSERT.
--
-- Эксперимент как задача, изготовление, образцы и фактические входы операций
-- отложены в optional-process-extension.sql; расширение в пилот не входит.
-- conditioning_event здесь описывает только записанную в карточке обработку.
--
-- Здесь обеспечены только указанные FK, UNIQUE и построчные CHECK.
-- Не реализованы глобальные проверки: отсутствие циклов result_dependency;
-- принадлежность input_group_id той же версии рецептуры; согласованность
-- источников/ячеек с листом карточки; соответствие типа/единицы метрике;
-- суммы состава и полнота групп; неизменяемость опубликованного плана;
-- версии исправлений, идемпотентный импорт и права.
-- Для них нужны прикладные транзакции, а там,
-- где необходимо, триггеры и защита от конкурентного изменения.
-- Историческая хронология не ограничивается CHECK: противоречивые даты
-- необходимо сохранять и разбирать, а не исправлять ради вставки.
-- Документация: https://www.postgresql.org/docs/18/ddl-constraints.html

BEGIN;

CREATE SCHEMA lims_example;

CREATE TABLE lims_example.source_file (
    id bigint PRIMARY KEY,
    original_path text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    date_system text NOT NULL CHECK (date_system IN ('1900', '1904'))
);

CREATE TABLE lims_example.source_sheet (
    id bigint PRIMARY KEY,
    file_id bigint NOT NULL REFERENCES lims_example.source_file(id) ON DELETE RESTRICT,
    name text NOT NULL,
    UNIQUE (file_id, name)
);

CREATE TABLE lims_example.source_cell (
    id bigint PRIMARY KEY,
    sheet_id bigint NOT NULL REFERENCES lims_example.source_sheet(id) ON DELETE RESTRICT,
    address text NOT NULL,
    cell_kind text NOT NULL,
    raw_value text,
    formula_text text,
    cached_value text,
    number_format text,
    UNIQUE (sheet_id, address),
    CHECK (formula_text IS NULL OR raw_value IS NULL),
    CHECK (formula_text IS NOT NULL OR cached_value IS NULL)
);

CREATE TABLE lims_example.mixture_type (
    id bigint PRIMARY KEY,
    code text NOT NULL UNIQUE,
    title text
);

CREATE TABLE lims_example.recipe_version (
    id bigint PRIMARY KEY,
    mixture_type_id bigint REFERENCES lims_example.mixture_type(id) ON DELETE RESTRICT,
    version_label text NOT NULL,
    recorded_on date,
    source_sheet_id bigint REFERENCES lims_example.source_sheet(id) ON DELETE RESTRICT
    -- version_label не уникален: Excel может содержать разные полные планы
    -- под одним обозначением. Внутренняя идентичность определяется id.
);

CREATE TABLE lims_example.recipe_group (
    id bigint PRIMARY KEY,
    recipe_version_id bigint NOT NULL REFERENCES lims_example.recipe_version(id) ON DELETE RESTRICT,
    label text NOT NULL,
    quantity_basis text NOT NULL CHECK (quantity_basis IN ('mass_percent', 'weight_parts', 'unknown')),
    completeness text NOT NULL CHECK (completeness IN ('unknown', 'complete', 'partial'))
);

CREATE TABLE lims_example.material (
    id bigint PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE lims_example.material_lot (
    id bigint PRIMARY KEY,
    material_id bigint NOT NULL REFERENCES lims_example.material(id) ON DELETE RESTRICT,
    lot_code text NOT NULL
    -- Номер сам по себе не уникален среди материалов/поставок.
);

CREATE TABLE lims_example.recipe_component (
    id bigint PRIMARY KEY,
    group_id bigint NOT NULL REFERENCES lims_example.recipe_group(id) ON DELETE RESTRICT,
    position integer NOT NULL CHECK (position > 0),
    unbatched_material_id bigint REFERENCES lims_example.material(id) ON DELETE RESTRICT,
    specified_lot_id bigint REFERENCES lims_example.material_lot(id) ON DELETE RESTRICT,
    name_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    quantity_value numeric CHECK (
        quantity_value >= 0
        AND quantity_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    quantity_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    CHECK (num_nonnulls(unbatched_material_id, specified_lot_id) <= 1)
    -- Материал известной партии выводится через material_lot.material_id.
    -- При неизвестной нормализации обе ссылки остаются NULL; имя остаётся
    -- в source_cell. quantity_value использует базу группы, без своей unit.
);

CREATE TABLE lims_example.filling_plan_line (
    id bigint PRIMARY KEY,
    recipe_version_id bigint NOT NULL REFERENCES lims_example.recipe_version(id) ON DELETE RESTRICT,
    input_group_id bigint REFERENCES lims_example.recipe_group(id) ON DELETE RESTRICT,
    unbatched_material_id bigint REFERENCES lims_example.material(id) ON DELETE RESTRICT,
    specified_lot_id bigint REFERENCES lims_example.material_lot(id) ON DELETE RESTRICT,
    label_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    mass_g numeric CHECK (
        mass_g >= 0
        AND mass_g NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    mass_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    mass_status text NOT NULL CHECK (
        mass_status IN ('recorded', 'calculation_unchecked', 'calculation_checked', 'unknown')
    ),
    CHECK (num_nonnulls(input_group_id, unbatched_material_id, specified_lot_id) <= 1),
    CHECK (
        (mass_status IN ('recorded', 'calculation_checked') AND mass_g IS NOT NULL)
        OR (mass_status IN ('calculation_unchecked', 'unknown') AND mass_g IS NULL)
    )
    -- Расчётная масса плана не становится фактической навеской.
    -- При calculation_unchecked кэш доступен только в source_cell.
);

CREATE TABLE lims_example.recipe_property (
    id bigint PRIMARY KEY,
    recipe_version_id bigint REFERENCES lims_example.recipe_version(id) ON DELETE RESTRICT,
    group_id bigint REFERENCES lims_example.recipe_group(id) ON DELETE RESTRICT,
    component_id bigint REFERENCES lims_example.recipe_component(id) ON DELETE RESTRICT,
    filling_plan_line_id bigint REFERENCES lims_example.filling_plan_line(id) ON DELETE RESTRICT,
    property_code text NOT NULL,
    value numeric NOT NULL CHECK (
        value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    unit_code text,
    source_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    CHECK (num_nonnulls(recipe_version_id, group_id, component_id, filling_plan_line_id) = 1)
    -- Свойство относится к записи конкретного плана: OHV/NCO/плотность
    -- не превращаются автоматически в свойства всей марки или партии.
);

CREATE TABLE lims_example.test_session (
    id bigint PRIMARY KEY,
    source_sheet_id bigint REFERENCES lims_example.source_sheet(id) ON DELETE RESTRICT,
    identifier_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    recorded_manufactured_on date,
    recorded_tested_on date
    -- Документальная карточка не создаёт образец или изготовление.
);

CREATE TABLE lims_example.recipe_match (
    session_id bigint NOT NULL REFERENCES lims_example.test_session(id) ON DELETE RESTRICT,
    recipe_version_id bigint NOT NULL REFERENCES lims_example.recipe_version(id) ON DELETE RESTRICT,
    status text NOT NULL CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    reason text NOT NULL,
    PRIMARY KEY (session_id, recipe_version_id)
    -- confirmed подтверждает только связь карточки с планом.
    -- Фактические навески и физическое происхождение этим не подтверждаются.
);

CREATE UNIQUE INDEX recipe_match_one_confirmed_per_session
    ON lims_example.recipe_match (session_id)
    WHERE status = 'confirmed';

CREATE TABLE lims_example.test_run (
    id bigint PRIMARY KEY,
    session_id bigint NOT NULL REFERENCES lims_example.test_session(id) ON DELETE RESTRICT,
    scope text NOT NULL,
    method_code text,
    scope_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT
    -- Физическая идентичность объекта не устанавливается в ядре пилота.
    -- method_code не объявляет неизвестную редакцию стандарта установленной.
);

CREATE TABLE lims_example.metric (
    id bigint PRIMARY KEY,
    code text NOT NULL UNIQUE,
    label text NOT NULL,
    unit_code text,
    value_kind text NOT NULL CHECK (value_kind IN ('numeric', 'text', 'ordinal'))
    -- Принятые значения данной метрики имеют одну единицу из unit_code.
    -- Исходная единица/формат сохраняются в ячейке; конверсия не реализована.
);

CREATE TABLE lims_example.session_condition (
    id bigint PRIMARY KEY,
    session_id bigint NOT NULL REFERENCES lims_example.test_session(id) ON DELETE RESTRICT,
    metric_id bigint NOT NULL REFERENCES lims_example.metric(id) ON DELETE RESTRICT,
    numeric_value numeric CHECK (
        numeric_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    text_value text,
    source_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    CHECK (num_nonnulls(numeric_value, text_value) = 1)
    -- Это только доказанно общие условия карточки; локальные условия
    -- отдельных методов требуют расширения, а не неявного наследования.
);

CREATE TABLE lims_example.result (
    id bigint PRIMARY KEY,
    test_run_id bigint NOT NULL REFERENCES lims_example.test_run(id) ON DELETE RESTRICT,
    metric_id bigint NOT NULL REFERENCES lims_example.metric(id) ON DELETE RESTRICT,
    numeric_value numeric CHECK (
        numeric_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    text_value text,
    origin text NOT NULL CHECK (origin IN ('recorded', 'calculated')),
    value_state text NOT NULL CHECK (
        value_state IN ('present', 'unparsed', 'missing_inputs', 'formula_unchecked')
    ),
    source_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    calculation_code text,
    CHECK (
        (value_state = 'present' AND num_nonnulls(numeric_value, text_value) = 1)
        OR (value_state = 'unparsed' AND numeric_value IS NULL AND text_value IS NOT NULL)
        OR (value_state IN ('missing_inputs', 'formula_unchecked')
            AND numeric_value IS NULL AND text_value IS NULL)
    ),
    CHECK (calculation_code IS NULL OR origin = 'calculated'),
    CHECK (value_state NOT IN ('missing_inputs', 'formula_unchecked') OR origin = 'calculated'),
    CHECK (origin <> 'calculated' OR value_state <> 'present' OR calculation_code IS NOT NULL)
    -- numeric_value не содержит непроверенный кэш: он остаётся в source_cell.
    -- Несколько строк одной метрики допускают отдельные определения;
    -- нельзя считать их независимыми изготовлениями только по числу строк.
);

CREATE TABLE lims_example.result_dependency (
    result_id bigint NOT NULL REFERENCES lims_example.result(id) ON DELETE RESTRICT,
    input_result_id bigint NOT NULL REFERENCES lims_example.result(id) ON DELETE RESTRICT,
    role text NOT NULL,
    PRIMARY KEY (result_id, role),
    CHECK (result_id <> input_result_id)
    -- Запрещена только прямая самоссылка; полный DAG требует отдельной
    -- проверки. role различает операнды формулы, например mass/length/width.
);

CREATE TABLE lims_example.observation (
    id bigint PRIMARY KEY,
    test_run_id bigint NOT NULL REFERENCES lims_example.test_run(id) ON DELETE RESTRICT,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    text_value text NOT NULL,
    source_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT
    -- Текст сохраняется дословно; последовательные ряды не становятся
    -- самостоятельными баллонами или независимыми повторами.
);

CREATE TABLE lims_example.conditioning_event (
    id bigint PRIMARY KEY,
    session_id bigint NOT NULL REFERENCES lims_example.test_session(id) ON DELETE RESTRICT,
    target_kind text NOT NULL CHECK (target_kind IN ('blend', 'can', 'specimen')),
    step_no integer NOT NULL CHECK (step_no > 0),
    kind text NOT NULL CHECK (kind IN ('aging', 'conditioning')),
    duration_value numeric CHECK (
        duration_value >= 0
        AND duration_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    duration_unit text,
    temperature_c numeric CHECK (
        temperature_c NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    source_cell_id bigint REFERENCES lims_example.source_cell(id) ON DELETE RESTRICT,
    CHECK ((duration_value IS NULL) = (duration_unit IS NULL))
    -- session_id означает записанную в карточке обработку.
    -- target_kind описывает её цель, а не установленный ID объекта.
    -- Календарные месяцы не преобразуются автоматически в сутки.
);

COMMIT;
