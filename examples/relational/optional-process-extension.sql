-- БУДУЩЕЕ РАСШИРЕНИЕ: в утверждённый пилот не входит.
-- Не запускать при развёртывании пилота и не включать в загрузку примеров.
-- Это отдельный набросок PostgreSQL 18 для возможного следующего этапа,
-- а не выполненная миграция и не подтверждение реального изготовления.
-- Он предполагает существующее 20-табличное ядро lims_example из schema.sql.
-- Применение требует отдельного решения; скрипт не идемпотентен.
--
-- Возвращает четыре отложенные таблицы и три связи из предметных записей.
-- Не вводит experiment_variant. Глобальные проверки происхождения, циклов,
-- соответствия типов объектов, единиц, прав и истории ещё не реализованы.
-- Ссылки не подтверждают фактические навески без независимых свидетельств.

BEGIN;

CREATE TABLE lims_example.experiment (
    id bigint PRIMARY KEY,
    title text NOT NULL,
    objective text
);

CREATE TABLE lims_example.preparation_run (
    id bigint PRIMARY KEY,
    experiment_id bigint REFERENCES lims_example.experiment(id) ON DELETE RESTRICT,
    kind text NOT NULL CHECK (kind IN ('blend', 'fill', 'apply', 'cut')),
    recipe_version_id bigint REFERENCES lims_example.recipe_version(id) ON DELETE RESTRICT,
    performed_on date,
    notes text
);

CREATE TABLE lims_example.sample (
    id bigint PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('blend_batch', 'filled_can', 'foam_block', 'specimen', 'external')),
    label text,
    produced_by_run_id bigint REFERENCES lims_example.preparation_run(id) ON DELETE RESTRICT
);

CREATE TABLE lims_example.preparation_input (
    id bigint PRIMARY KEY,
    preparation_run_id bigint NOT NULL REFERENCES lims_example.preparation_run(id) ON DELETE RESTRICT,
    source_sample_id bigint REFERENCES lims_example.sample(id) ON DELETE RESTRICT,
    material_lot_id bigint REFERENCES lims_example.material_lot(id) ON DELETE RESTRICT,
    unbatched_material_id bigint REFERENCES lims_example.material(id) ON DELETE RESTRICT,
    input_label text,
    actual_mass_g numeric CHECK (
        actual_mass_g >= 0
        AND actual_mass_g NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    CHECK (num_nonnulls(source_sample_id, material_lot_id, unbatched_material_id) <= 1),
    CHECK (
        num_nonnulls(source_sample_id, material_lot_id, unbatched_material_id) = 1
        OR (input_label IS NOT NULL AND btrim(input_label) <> '')
    )
    -- actual_mass_g относится ко всему запуску. Для индивидуальных навесок
    -- в баллоны нужен отдельный fill-запуск на каждый баллон.
    -- Неподтверждённые фактические массы остаются NULL; план не копируется.
);

ALTER TABLE lims_example.test_session
    ADD COLUMN experiment_id bigint
        REFERENCES lims_example.experiment(id) ON DELETE RESTRICT;

ALTER TABLE lims_example.test_run
    ADD COLUMN sample_id bigint
        REFERENCES lims_example.sample(id) ON DELETE RESTRICT;

ALTER TABLE lims_example.conditioning_event
    ADD COLUMN sample_id bigint
        REFERENCES lims_example.sample(id) ON DELETE RESTRICT,
    ALTER COLUMN session_id DROP NOT NULL,
    ADD CONSTRAINT conditioning_event_one_owner
        CHECK (num_nonnulls(session_id, sample_id) = 1);
    -- session_id: записанная в карточке обработка неизвестного объекта.
    -- sample_id: обработка установленного физического объекта.
    -- Существующие строки ядра сохраняются с session_id и sample_id=NULL.

COMMIT;
