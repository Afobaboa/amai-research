# Конкретные строки демонстрационной БД

Сгенерировано из первичных Excel. ID назначены только для этого примера; это не лабораторные номера.

Все числа приведены без скрытой перенормировки и округления. Длинные десятичные хвосты — запись XLSX.

Для source_cell показана выборка; все ячейки находятся в rows.json и data.sql. Здесь только 20 таблиц ядра пилота; физический процесс вынесен в optional-process-extension.sql и в эти строки не входит.

## source_file

| id | original_path | sha256 | date_system |
| --- | --- | --- | --- |
| 1 | Пена/pena/Разработка рецептур/316 ЗИМНЯЯ УЛУЧШЕННАЯ.xlsx | 017e937fd969bb011e1fb63dce6d2108de51357a2cd5515897a5597a3bc9a7a6 | 1900 |
| 2 | Пена/pena/Разработка рецептур/STP рецептура.xlsx | 762f71554e88adf33c11a41939f4eb441096158c019fe9e5eb21c521ac0e5591 | 1900 |
| 3 | Пена/pena/Испытания разработки рецептур/316.xlsx | 6748bc1f405294aa45ad7e2e9ea9812bd61fd9047219950c1cda251a5528b11f | 1900 |
| 4 | Пена/pena/Испытания разработки рецептур/302.xlsx | 56e87f09c8fa38457e3b595a9364ae979c0ae07384c0a6d2b239c37cadceeffb | 1900 |
| 5 | Пена/pena/Испытания разработки рецептур/Проверка нового сырья/HJC.xlsx | c0cb26580c2526c7322ee3e0db8647ff2d367b5245c7177f62b21982c32e0077 | 1900 |

## source_sheet

| id | file_id | name |
| --- | --- | --- |
| 101 | 1 | 316.8 |
| 102 | 1 | 316.26 |
| 103 | 2 | 40 |
| 104 | 2 | 43 |
| 201 | 3 | 316.8 |
| 202 | 3 | 316.8 (2) |
| 203 | 3 | 316.17 стар.пол. |
| 204 | 4 | 302.19(ст.бал) |
| 205 | 5 | 830 ст.пол+ст.бал |

## source_cell

| id | sheet_id | address | cell_kind | raw_value | formula_text | cached_value | number_format |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 101 | A4 | s | Puranol D210 п.BPD6126523 | NULL | NULL | General |
| 25 | 101 | B4 | n | 113.67 | NULL | NULL | 0.0 |
| 27 | 101 | D4 | n | 12.8 | NULL | NULL | 0.00 |
| 295 | 101 | F33 | n | NULL | ROUND(G33*B23,1) | 279.7 | 0.00_) |
| 863 | 103 | D1 | s | STP 40 | NULL | NULL | General |
| 1092 | 104 | D1 | s | STP 40 | NULL | NULL | General |
| 1336 | 201 | G6 | n | NULL | C6-E6 | 759.16000000000008 | 0.0 |
| 1353 | 201 | B14 | n | 5 | NULL | NULL | General |
| 1445 | 202 | B14 | n | 6 | NULL | NULL | General |
| 1541 | 203 | F16 | n | NULL | ((3-B16)/3)*100 | 100 | 0.00 |
| 1760 | 203 | B16 | blank | NULL | NULL | NULL | General |

## mixture_type

| id | code | title |
| --- | --- | --- |
| 1 | 316 | Зимняя пена — рабочая трактовка типа смеси |
| 2 | STP | STP — название семейства книги |

## recipe_version

| id | mixture_type_id | version_label | recorded_on | source_sheet_id |
| --- | --- | --- | --- | --- |
| 1 | 1 | 8 | 2026-08-18 | 101 |
| 2 | 1 | 26 | 2026-09-08 | 102 |
| 3 | 2 | 40 | 2024-05-20 | 103 |
| 4 | 2 | 40 | 2024-05-20 | 104 |

## recipe_group

| id | recipe_version_id | label | quantity_basis | completeness |
| --- | --- | --- | --- | --- |
| 1 | 1 | Recipe for polyol | mass_percent | unknown |
| 2 | 2 | Recipe for polyol | mass_percent | unknown |
| 3 | 3 | Recipe for polyol | mass_percent | unknown |
| 4 | 4 | Recipe for polyol | mass_percent | unknown |

## material

| id | name |
| --- | --- |
| 1 | Puranol D210 |
| 2 | YD- C310 |
| 3 | YD-307 |
| 4 | TCPP |
| 5 | DMDEЕ |
| 6 | HGD-8806 |
| 7 | Metastab AK-88906 |
| 8 | Dismadur |
| 9 | DME |
| 10 | F134a |
| 11 | Prop/IB |
| 12 | Inovol C210 |
| 13 | Inovol C307 |
| 14 | Metastab AK-8872 |
| 15 | Wannate pm-200 |
| 16 | Kaneka SAX590 (4,8-11,5 Pa*s) |
| 17 | Risun 30000 |
| 18 | Risun 12000T |
| 19 | TEP |
| 20 | DPG DME |
| 21 | ДБТА |
| 22 | TibKat423 |
| 23 | Сыр-D90 (ВТМЭКмоС) |
| 24 | HTC-108 |
| 25 | CSF 6350 |
| 26 | LT-792 |
| 27 | NIAX L-5351 |

## material_lot

| id | material_id | lot_code |
| --- | --- | --- |
| 1 | 1 | BPD6126523 |
| 2 | 2 | 26041502 |
| 3 | 3 | 26041904 |
| 4 | 4 | JSAC20260423 |
| 5 | 5 | 1162026020206 |
| 6 | 7 | 260130-1-3-5 |
| 7 | 12 | 260613G |
| 8 | 2 | 26062309 |
| 9 | 13 | 260501А |

## recipe_component

| id | group_id | position | unbatched_material_id | specified_lot_id | name_cell_id | quantity_value | quantity_cell_id |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 4 | NULL | 1 | 24 | 12.8 | 27 |
| 2 | 1 | 5 | NULL | 2 | 40 | 17.7 | 43 |
| 3 | 1 | 6 | NULL | 3 | 55 | 26.4 | 58 |
| 4 | 1 | 13 | NULL | 4 | 130 | 36.5 | 132 |
| 5 | 1 | 14 | NULL | 5 | 136 | 1.1000000000000001 | 138 |
| 6 | 1 | 16 | 6 | NULL | 153 | 4 | 155 |
| 7 | 1 | 18 | NULL | 6 | 178 | 1.5 | 180 |
| 8 | 2 | 4 | NULL | 7 | 454 | 12.8 | 457 |
| 9 | 2 | 5 | NULL | 8 | 470 | 17.7 | 473 |
| 10 | 2 | 6 | NULL | 9 | 485 | 26.4 | 488 |
| 11 | 2 | 13 | NULL | 4 | 560 | 36.5 | 562 |
| 12 | 2 | 14 | NULL | 5 | 566 | 1.1000000000000001 | 568 |
| 13 | 2 | 16 | 14 | NULL | 583 | 1.4 | 585 |
| 14 | 2 | 18 | NULL | 6 | 608 | 4.0999999999999996 | 610 |
| 15 | 3 | 4 | 16 | NULL | 868 | 0 | 869 |
| 16 | 3 | 5 | 17 | NULL | 872 | 0 | 873 |
| 17 | 3 | 6 | 18 | NULL | 876 | 73 | 877 |
| 18 | 3 | 7 | 4 | NULL | 880 | 10 | 881 |
| 19 | 3 | 8 | 19 | NULL | 884 | 0 | 885 |
| 20 | 3 | 9 | 20 | NULL | 888 | 0 | 889 |
| 21 | 3 | 10 | 21 | NULL | 892 | 5 | 893 |
| 22 | 3 | 11 | 22 | NULL | 896 | 0 | 897 |
| 23 | 3 | 12 | 23 | NULL | 900 | 0 | 901 |
| 24 | 3 | 13 | 24 | NULL | 904 | 0 | 905 |
| 25 | 3 | 14 | 25 | NULL | 908 | 0 | 909 |
| 26 | 3 | 15 | 26 | NULL | 912 | 5 | 913 |
| 27 | 3 | 16 | 27 | NULL | 916 | 7 | 917 |
| 28 | 3 | 17 | 7 | NULL | 920 | 0 | 921 |
| 29 | 4 | 4 | 16 | NULL | 1097 | 0 | 1098 |
| 30 | 4 | 5 | 17 | NULL | 1101 | 0 | 1102 |
| 31 | 4 | 6 | 18 | NULL | 1105 | 73 | 1106 |
| 32 | 4 | 7 | 4 | NULL | 1109 | 10 | 1110 |
| 33 | 4 | 8 | 19 | NULL | 1113 | 0 | 1114 |
| 34 | 4 | 9 | 20 | NULL | 1117 | 0 | 1118 |
| 35 | 4 | 10 | 21 | NULL | 1121 | 5 | 1122 |
| 36 | 4 | 11 | 22 | NULL | 1125 | 0 | 1126 |
| 37 | 4 | 12 | 23 | NULL | 1129 | 0 | 1130 |
| 38 | 4 | 13 | 24 | NULL | 1133 | 0 | 1134 |
| 39 | 4 | 14 | 25 | NULL | 1137 | 0 | 1138 |
| 40 | 4 | 15 | 26 | NULL | 1141 | 5 | 1142 |
| 41 | 4 | 16 | 27 | NULL | 1145 | 7 | 1146 |
| 42 | 4 | 17 | 7 | NULL | 1149 | 0 | 1150 |

## filling_plan_line

| id | recipe_version_id | input_group_id | unbatched_material_id | specified_lot_id | label_cell_id | mass_g | mass_cell_id | mass_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1 | NULL | NULL | 294 | NULL | 295 | calculation_unchecked |
| 2 | 1 | NULL | 8 | NULL | 306 | NULL | 307 | calculation_unchecked |
| 3 | 1 | NULL | 9 | NULL | 323 | NULL | 324 | calculation_unchecked |
| 4 | 1 | NULL | 10 | NULL | 331 | NULL | 332 | calculation_unchecked |
| 5 | 1 | NULL | 11 | NULL | 339 | NULL | 340 | calculation_unchecked |
| 6 | 2 | 2 | NULL | NULL | 724 | NULL | 725 | calculation_unchecked |
| 7 | 2 | NULL | 15 | NULL | 736 | NULL | 737 | calculation_unchecked |
| 8 | 2 | NULL | 9 | NULL | 753 | NULL | 754 | calculation_unchecked |
| 9 | 2 | NULL | 10 | NULL | 761 | NULL | 762 | calculation_unchecked |
| 10 | 2 | NULL | 11 | NULL | 769 | NULL | 770 | calculation_unchecked |
| 11 | 3 | 3 | NULL | NULL | 996 | NULL | 997 | calculation_unchecked |
| 12 | 3 | NULL | 9 | NULL | 1010 | NULL | 1011 | calculation_unchecked |
| 13 | 3 | NULL | NULL | NULL | 1016 | NULL | 1017 | calculation_unchecked |
| 14 | 3 | NULL | 11 | NULL | 1022 | NULL | 1023 | calculation_unchecked |
| 15 | 4 | 4 | NULL | NULL | 1224 | NULL | 1225 | calculation_unchecked |
| 16 | 4 | NULL | 9 | NULL | 1238 | NULL | 1239 | calculation_unchecked |
| 17 | 4 | NULL | NULL | NULL | 1244 | NULL | 1245 | calculation_unchecked |
| 18 | 4 | NULL | 11 | NULL | 1250 | NULL | 1251 | calculation_unchecked |

## recipe_property

| id | recipe_version_id | group_id | component_id | filling_plan_line_id | property_code | value | unit_code | source_cell_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | NULL | NULL | 1 | NULL | ohv | 113.67 | mg_KOH/g | 25 |
| 2 | NULL | NULL | 2 | NULL | ohv | 166.9 | mg_KOH/g | 41 |
| 3 | NULL | NULL | 3 | NULL | ohv | 238.5 | mg_KOH/g | 56 |
| 4 | NULL | 1 | NULL | NULL | preparation_scale | 350 | NULL | 17 |
| 5 | 1 | NULL | NULL | NULL | gas_mass_pct | 19.399999999999999 | mass_percent | 280 |
| 6 | 1 | NULL | NULL | NULL | target_fill_volume | 802 | mL | 286 |
| 7 | 1 | NULL | NULL | NULL | nominal_can_volume | 1000 | mL | 391 |
| 8 | NULL | NULL | NULL | 1 | reference_ab_mass | 100 | g | 226 |
| 9 | NULL | NULL | NULL | 2 | reference_ab_mass | 128 | g | 234 |
| 10 | NULL | NULL | NULL | 2 | nco | 31.06 | mass_percent | 9 |
| 11 | NULL | NULL | NULL | 3 | reference_gas_mass_pct | 46.2 | mass_percent | 230 |
| 12 | NULL | NULL | NULL | 4 | reference_gas_mass_pct | 0 | mass_percent | 238 |
| 13 | NULL | NULL | NULL | 5 | reference_gas_mass_pct | 53.8 | mass_percent | 247 |
| 14 | NULL | NULL | 8 | NULL | ohv | 112 | mg_KOH/g | 455 |
| 15 | NULL | NULL | 9 | NULL | ohv | 167.8 | mg_KOH/g | 471 |
| 16 | NULL | NULL | 10 | NULL | ohv | 239.3 | mg_KOH/g | 486 |
| 17 | NULL | 2 | NULL | NULL | preparation_scale | 350 | NULL | 447 |
| 18 | 2 | NULL | NULL | NULL | gas_mass_pct | 19.399999999999999 | mass_percent | 710 |
| 19 | 2 | NULL | NULL | NULL | target_fill_volume | 802 | mL | 716 |
| 20 | 2 | NULL | NULL | NULL | nominal_can_volume | 1000 | mL | 821 |
| 21 | NULL | NULL | NULL | 6 | reference_ab_mass | 100 | g | 656 |
| 22 | NULL | NULL | NULL | 7 | reference_ab_mass | 128 | g | 664 |
| 23 | NULL | NULL | NULL | 7 | nco | 31.05 | mass_percent | 439 |
| 24 | NULL | NULL | NULL | 8 | reference_gas_mass_pct | 46.2 | mass_percent | 660 |
| 25 | NULL | NULL | NULL | 9 | reference_gas_mass_pct | 0 | mass_percent | 668 |
| 26 | NULL | NULL | NULL | 10 | reference_gas_mass_pct | 53.8 | mass_percent | 677 |
| 27 | NULL | 3 | NULL | NULL | preparation_scale | 400 | NULL | 866 |
| 28 | 3 | NULL | NULL | NULL | gas_mass_pct | 15 | mass_percent | 986 |
| 29 | 3 | NULL | NULL | NULL | target_fill_volume | 453 | mL | 992 |
| 30 | NULL | NULL | NULL | 12 | reference_gas_mass_pct | 45 | mass_percent | 941 |
| 31 | NULL | NULL | NULL | 13 | reference_gas_mass_pct | 0 | mass_percent | 946 |
| 32 | NULL | NULL | NULL | 14 | reference_gas_mass_pct | 55 | mass_percent | 954 |
| 33 | NULL | 4 | NULL | NULL | preparation_scale | 400 | NULL | 1095 |
| 34 | 4 | NULL | NULL | NULL | gas_mass_pct | 16 | mass_percent | 1214 |
| 35 | 4 | NULL | NULL | NULL | target_fill_volume | 457 | mL | 1220 |
| 36 | NULL | NULL | NULL | 16 | reference_gas_mass_pct | 45 | mass_percent | 1170 |
| 37 | NULL | NULL | NULL | 17 | reference_gas_mass_pct | 0 | mass_percent | 1174 |
| 38 | NULL | NULL | NULL | 18 | reference_gas_mass_pct | 55 | mass_percent | 1182 |

## test_session

| id | source_sheet_id | identifier_cell_id | recorded_manufactured_on | recorded_tested_on |
| --- | --- | --- | --- | --- |
| 1 | 201 | 1325 | 2026-08-04 | 2026-08-14 |
| 2 | 202 | 1417 | 2026-08-18 | 2026-08-19 |
| 3 | 203 | 1508 | 2026-09-03 | 2026-09-04 |
| 4 | 204 | 1594 | 2021-11-09 | 2021-12-10 |
| 5 | 205 | 1679 | 2022-09-21 | 2022-09-27 |

## recipe_match

| session_id | recipe_version_id | status | reason |
| --- | --- | --- | --- |
| 1 | 1 | candidate | Совпадает обозначение 316.8; дата карточки раньше даты найденного листа плана. |
| 2 | 1 | candidate | Совпадает обозначение 316.8; фактический состав не установлен. |

## test_run

| id | session_id | scope | method_code | scope_cell_id |
| --- | --- | --- | --- | --- |
| 1 | 1 | paper | NULL | 1347 |
| 2 | 1 | can_checks | NULL | 1330 |
| 3 | 2 | paper | NULL | 1439 |
| 4 | 2 | can_checks | NULL | 1422 |
| 5 | 3 | paper | NULL | 1530 |
| 6 | 4 | paper | NULL | 1613 |
| 7 | 5 | paper | NULL | 1698 |
| 8 | 5 | can_checks | NULL | 1696 |

## metric

| id | code | label | unit_code | value_kind |
| --- | --- | --- | --- | --- |
| 1 | ambient_temperature | Температура окружающей среды | degC | numeric |
| 2 | relative_humidity | Относительная влажность | percent | numeric |
| 3 | tack_free | Время отлипа | min | numeric |
| 4 | cut_time | Время резки | min | numeric |
| 5 | can_mass_before_release | Масса до выпуска | g | numeric |
| 6 | can_mass_after_release | Масса после выпуска | g | numeric |
| 7 | released_mass | Масса выпуска | g | numeric |
| 8 | shrinkage | Усадка | percent | numeric |
| 9 | density | Плотность ядра | g/dm3 | numeric |
| 10 | calculated_yield | Расчётный выход | L | numeric |
| 11 | shakeability | Встряхиваемость по исходной шкале | NULL | ordinal |

## session_condition

| id | session_id | metric_id | numeric_value | text_value | source_cell_id |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1 | 25.4 | NULL | 1342 |
| 2 | 1 | 2 | 39.1 | NULL | 1344 |
| 3 | 2 | 1 | 23 | NULL | 1434 |
| 4 | 2 | 2 | 47.2 | NULL | 1436 |
| 5 | 3 | 1 | 24.2 | NULL | 1525 |
| 6 | 3 | 2 | 45 | NULL | 1527 |
| 7 | 4 | 1 | 25.2 | NULL | 1608 |
| 8 | 4 | 2 | 19.399999999999999 | NULL | 1610 |
| 9 | 5 | 1 | 24.7 | NULL | 1693 |
| 10 | 5 | 2 | 35.799999999999997 | NULL | 1695 |

## result

| id | test_run_id | metric_id | numeric_value | text_value | origin | value_state | source_cell_id | calculation_code |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 3 | 5 | NULL | recorded | present | 1353 | NULL |
| 2 | 1 | 4 | 20 | NULL | recorded | present | 1355 | NULL |
| 3 | 2 | 5 | 929.96 | NULL | recorded | present | 1332 | NULL |
| 4 | 2 | 6 | 170.8 | NULL | recorded | present | 1334 | NULL |
| 5 | 2 | 7 | 759.16 | NULL | calculated | present | 1336 | subtract_two_g_values_v1 |
| 6 | 1 | 8 | NULL | NULL | calculated | formula_unchecked | 1359 | NULL |
| 7 | 1 | 9 | NULL | NULL | calculated | formula_unchecked | 1370 | NULL |
| 8 | 1 | 10 | NULL | NULL | calculated | formula_unchecked | 1380 | NULL |
| 9 | 3 | 3 | 6 | NULL | recorded | present | 1445 | NULL |
| 10 | 3 | 4 | 21 | NULL | recorded | present | 1447 | NULL |
| 11 | 4 | 5 | 931.9 | NULL | recorded | present | 1424 | NULL |
| 12 | 4 | 6 | 162.66999999999999 | NULL | recorded | present | 1426 | NULL |
| 13 | 4 | 7 | 769.23000000000001 | NULL | calculated | present | 1428 | subtract_two_g_values_v1 |
| 14 | 3 | 8 | NULL | NULL | calculated | formula_unchecked | 1451 | NULL |
| 15 | 3 | 9 | NULL | NULL | calculated | formula_unchecked | 1461 | NULL |
| 16 | 3 | 10 | NULL | NULL | calculated | formula_unchecked | 1471 | NULL |
| 17 | 5 | 3 | 6 | NULL | recorded | present | 1536 | NULL |
| 18 | 5 | 4 | 18 | NULL | recorded | present | 1538 | NULL |
| 19 | 5 | 8 | NULL | NULL | calculated | missing_inputs | 1541 | NULL |
| 20 | 6 | 3 | 6 | NULL | recorded | present | 1619 | NULL |
| 21 | 6 | 4 | 18 | NULL | recorded | present | 1621 | NULL |
| 22 | 7 | 3 | 5 | NULL | recorded | present | 1704 | NULL |
| 23 | 7 | 4 | 18 | NULL | recorded | present | 1706 | NULL |
| 24 | 8 | 11 | NULL | 5- | recorded | present | 1697 | NULL |

## result_dependency

| result_id | input_result_id | role |
| --- | --- | --- |
| 5 | 3 | before |
| 5 | 4 | after |
| 13 | 11 | before |
| 13 | 12 | after |

## observation

| id | test_run_id | sequence_no | text_value | source_cell_id |
| --- | --- | --- | --- | --- |
| 1 | 1 | 1 | Блок не усел. Структура хорошая, на -18С хуже всего. | 1362 |
| 2 | 1 | 2 | 1р - первич расш больше среднего, выходит равномерно, легко, вязковатая, трещит. | 1363 |
| 3 | 1 | 3 | 2р - после встрях выходит мягче, поверхность не такая грубая, ранвомерно. | 1364 |
| 4 | 1 | 4 | 3р - после встрях выходит как во 2 ряду, мягко, с 6 зм  плотнее, к концу ряда вязкость выравнилась. | 1365 |
| 5 | 1 | 5 | 4р - после встрях без изменений. | 1366 |
| 6 | 3 | 2 | 1р - первич расш больше среднего, выходит равномерно, легко, плотная. | 1454 |
| 7 | 3 | 3 | 2р - после встрях без изменений. | 1455 |
| 8 | 3 | 4 | 3р - после встрях без изменений. | 1456 |
| 9 | 3 | 5 | 4р - после встрях без изменений, с 5 змейки вязче, с 6 зм шумит. | 1457 |

## conditioning_event

| id | session_id | target_kind | step_no | kind | duration_value | duration_unit | temperature_c | source_cell_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 4 | blend | 1 | aging | NULL | NULL | NULL | 1594 |
| 2 | 4 | can | 2 | aging | NULL | NULL | NULL | 1594 |
| 3 | 5 | blend | 1 | aging | NULL | NULL | NULL | 1679 |
| 4 | 5 | can | 2 | aging | NULL | NULL | NULL | 1679 |
