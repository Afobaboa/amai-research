"""Read the three winter test-card layouts without trusting formula caches."""

import ast
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import operator
import re


REFERENCE = re.compile(r"\$?([A-Z]+)\$?(\d+)")
NUMBER = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?")
OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
}


class MissingInputs(ValueError):
    """A formula operand has no recorded value."""


def raw(book, address):
    return book["cells"].get(address, {}).get("v")


def address_order(address):
    match = re.fullmatch(r"([A-Z]+)(\d+)", address)
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    return int(match[2]), column


def numeric(value):
    if value is None or not NUMBER.fullmatch(str(value).strip()):
        return None
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def arithmetic(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        return OPERATORS[type(node.op)](arithmetic(node.left), arithmetic(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return arithmetic(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id.upper() == "ROUND" and len(node.args) == 2:
            value, digits = (arithmetic(arg) for arg in node.args)
            return value.quantize(Decimal(10) ** -int(digits), rounding=ROUND_HALF_UP)
    raise ValueError("Unsupported arithmetic")


def evaluate(book, address, dependencies, seen=()):
    if address in seen:
        raise ValueError("Circular formula")
    if address not in dependencies:
        dependencies.append(address)
    cell = book["cells"].get(address, {})
    formula = cell.get("f")
    if formula:
        def replace(match):
            value = evaluate(book, "".join(match.groups()), dependencies,
                             (*seen, address))
            return f"({value})"
        expression = REFERENCE.sub(replace, formula.replace("^", "**"))
        value = arithmetic(ast.parse(expression, mode="eval").body)
        cached = numeric(cell.get("v"))
        if cached is None or not math.isclose(value, cached, rel_tol=1e-8,
                                              abs_tol=1e-7):
            raise ValueError("Formula cache differs from recorded operands")
        return cached
    if cell.get("v") is None or not str(cell["v"]).strip():
        raise MissingInputs(address)
    value = numeric(cell["v"])
    if value is None or cell.get("t") == "e" or cell.get("date_style"):
        raise ValueError("Not a recorded number")
    return value


def date_value(book, address):
    value = raw(book, address)
    if value is None or not str(value).strip():
        return None
    number = numeric(value)
    if number is not None:
        epoch = date(1904, 1, 1) if book["epoch1904"] else date(1899, 12, 30)
        try:
            value = epoch + timedelta(days=int(number))
            return value.isoformat() if date(1990, 1, 1) <= value else None
        except (ValueError, OverflowError):
            return None
    for pattern in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), pattern).date().isoformat()
        except ValueError:
            pass
    return None


def result(book, address, code, title, unit=None, text=False):
    cell = book["cells"].get(address, {})
    if cell.get("v") is None and not cell.get("f"):
        return None
    if not cell.get("f") and not str(cell.get("v", "")).strip():
        return None
    record = dict(metric_code=code, metric_title=title, unit=unit,
                  value_numeric=None, value_text=None, source_cell=address,
                  origin="calculated" if cell.get("f") else "recorded",
                  value_state="present", formula_text=cell.get("f"), dependencies=[])
    if text and not cell.get("f"):
        record["value_text"] = str(cell["v"])
        return record
    try:
        value = evaluate(book, address, record["dependencies"])
        record["value_numeric"] = str(value)
    except MissingInputs:
        record["value_state"] = "missing_inputs"
    except (ValueError, ArithmeticError, TypeError, OverflowError, SyntaxError):
        record["value_state"] = "formula_unchecked" if cell.get("f") else "unparsed"
        if not cell.get("f"):
            record["value_text"] = str(cell.get("v", ""))
    return record


def row_labels(book):
    return {int(address[1:]): str(cell.get("v") or "")
            for address, cell in book["cells"].items()
            if re.fullmatch(r"A\d+", address)}


def condition_definition(label):
    key = label.casefold()
    if key.startswith("температура окр."):
        return "ambient_temperature", "Температура окружающей среды", "°C"
    if key.startswith("температура баллона"):
        return "can_temperature", "Температура баллона", "°C"
    if key.startswith("влажность"):
        return "relative_humidity", "Относительная влажность", "%"
    if key.startswith("давление в баллоне"):
        return "can_pressure", "Давление в баллоне", "атм"
    return None


def extract_conditions(book, labels):
    conditions = []
    for row, label in labels.items():
        definition = condition_definition(label)
        value = raw(book, f"B{row}")
        if definition is None or value is None or not str(value).strip():
            continue
        code, title, unit = definition
        number = numeric(value)
        conditions.append(dict(
            code=code, title=title, unit=unit, cell=f"B{row}",
            value_numeric=str(number) if number is not None else None,
            value_text=str(value) if number is None else None,
        ))
    return conditions


def runs(book):
    scopes = {"card": ("Параметры карточки", None, None),
              "paper": ("Испытание на бумаге", None, None),
              "mold": ("Испытание в форме", None, None),
              "other": ("Дополнительные показатели", None, None)}
    for address, cell in book["cells"].items():
        value = str(cell.get("v") or "")
        key = "paper" if "ТЕСТ НА БУМАГЕ" in value else "mold"
        if "ТЕСТ НА БУМАГЕ" in value or "ТЕСТ В ФОРМЕ" in value:
            title, method, _ = scopes[key]
            scopes[key] = title, method, address
    return {key: dict(scope=title, method_code=method, scope_cell=address,
                      results=[], observations=[])
            for key, (title, method, address) in scopes.items()}


def row_scope(row, output, labels):
    paper = output["paper"]["scope_cell"]
    mold = output["mold"]["scope_cell"]
    paper_row = address_order(paper)[0] if paper else 12
    mold_row = address_order(mold)[0] if mold else 1000000
    other_row = min((r for r, label in labels.items()
                     if label.casefold().startswith("адгезия")), default=1000000)
    return ("card" if row < paper_row else "paper" if row < mold_row
            else "mold" if row < other_row else "other")


def metric_definition(label, scope):
    key = label.casefold()
    scope_title = {"paper": "на бумаге", "mold": "в форме"}.get(scope, scope)
    definitions = [
        ("время отлипа", "tack_free", "Время отлипа", "мин"),
        ("время резки", "cut_time", "Время резки", "мин"),
        ("плотность (в ядре)", f"density_{scope}",
         f"Плотность в ядре ({scope_title})", "кг/м³"),
        ("выход (расчетный)", f"yield_{scope}",
         f"Выход пены (расчётный, {scope_title})", "л"),
        ("выход в куб", "yield_tm1003", "Выход в куб, ТМ1003", "л"),
        ("вторичное расширение тм1010", "expansion_tm1010",
         "Вторичное расширение, ТМ1010", "%"),
        ("стабильность размеров тм1004", "stability_tm1004",
         "Стабильность размеров, ТМ1004", "%"),
        ("масса нетто", "recorded_net_mass", "Масса нетто по записи", "г"),
        ("масса брутто", "recorded_gross_mass", "Масса брутто по записи", "г"),
        ("масса куба", "cube_mass", "Масса куба", "г"),
    ]
    return next((definition[1:] for definition in definitions
                 if key.startswith(definition[0])), None)


def extra_metric_definition(label, scope):
    key = label.casefold()
    if key.startswith('выход в "змейках"'):
        return f"bead_count_{scope}", 'Выход в «змейках»', "шт."
    if key.startswith("встряхиваемость"):
        return "shakeability", "Встряхиваемость по шкале 0–5", None
    if key.startswith("хрупкость"):
        return "fragility_tm1008", "Хрупкость пены, ТМ1008", None
    if key.startswith("адгезия"):
        return "vertical_adhesion", "Адгезия к вертикальной поверхности", None
    return None


def append_row_result(book, row, label, scope, output, consumed):
    definition = (metric_definition(label, scope)
                  or extra_metric_definition(label, scope))
    if definition is None:
        return
    code, title, unit = definition
    address = f"B{row}"
    if code == "yield_tm1003" and raw(book, f"C{row}") is not None:
        address = f"C{row}"
    if scope == "other" and code.startswith("bead_count"):
        address = f"C{row}" if raw(book, f"C{row}") is not None else address
    text = code in {"shakeability", "fragility_tm1008", "vertical_adhesion"}
    record = result(book, address, code, title, unit, text=text)
    if record:
        if scope == "other" and code.startswith("bead_count"):
            record.update(value_numeric=None, value_text=str(raw(book, address)),
                          value_state="unparsed", unit=None,
                          metric_title="Дополнительная запись выхода в «змейках»")
        output[scope]["results"].append(record)
        consumed.add(address)


def shrinkage_metric(book, row):
    for address, cell in book["cells"].items():
        value = str(cell.get("v") or "")
        if address_order(address)[0] != row or cell.get("t") not in {"s", "str"}:
            continue
        match = re.fullmatch(r"\s*(\d+)\s*сут(?:ки)?\s*", value)
        if match:
            return (f"shrinkage_paper_day_{match[1]}",
                    f"Усадка пены, срок измерения: {match[1]} сут")
    return "shrinkage_paper", "Усадка пены (срок не указан)"


def append_shrinkage(book, output, consumed):
    for address, cell in book["cells"].items():
        formula = cell.get("f") or ""
        row, column = address_order(address)
        if row not in (15, 16) or column < 4:
            continue
        if not re.search(r"\bB(?:15|16)\b", formula):
            continue
        if not re.search(r"/3\)?\*100", formula.replace(" ", "")):
            continue
        code, title = shrinkage_metric(book, row)
        record = result(book, address, code, title, "%")
        output["paper"]["results"].append(record)
        consumed.add(address)


def append_mass_results(book, output, consumed):
    for address, code, title in [
            ("C6", "can_mass_before", "Масса баллона до выпуска"),
            ("E6", "can_mass_after", "Масса баллона после выпуска")]:
        record = result(book, address, code, title, "г")
        if record:
            output["card"]["results"].append(record)
            consumed.add(address)
    for address in ("G6", "I6"):
        cell = book["cells"].get(address, {})
        if not cell.get("f"):
            continue
        record = result(book, address, "dispensed_mass", "Масса выпуска", "г")
        output["card"]["results"].append(record)
        consumed.add(address)


def observation_kind(label):
    key = label.casefold()
    if key.startswith("пов-ть"):
        return "surface"
    if key.startswith("структура"):
        return "structure"
    if key.startswith("особенности поведения"):
        return "behavior"
    return "general"


def add_observations(book, labels, output, consumed):
    headings = {"масса", "масса, г", "длина", "ширина", "высота", "%",
                "t℃ бал", "t℃ щел", "ширина щели, мм", "результат",
                "общая плотность", "кг/м³", "потеря массы", "рассчет, %"}
    ordered = sorted(book["cells"].items(), key=lambda item: address_order(item[0]))
    for address, cell in ordered:
        row, column = address_order(address)
        value = cell.get("v")
        if row < 12 or column == 1 or address in consumed or cell.get("f"):
            continue
        if (cell.get("t") not in {"s", "inlineStr", "str"}
                or not str(value or "").strip()):
            continue
        if str(value).strip().casefold() in headings or "ТЕСТ " in str(value):
            continue
        prior_row = max((r for r in labels if r <= row), default=0)
        scope = row_scope(row, output, labels)
        output[scope]["observations"].append(dict(text=str(value),
            source_cell=address, kind=observation_kind(labels.get(prior_row, ""))))
    for address, text in book.get("comments", {}).items():
        scope = row_scope(address_order(address)[0], output, labels)
        output[scope]["observations"].append(dict(text=text,
            source_cell=address, kind="excel_comment"))


def notes(book, manufactured, tested):
    messages = ["Перенесено из исходной карточки Excel."]
    for address, parsed, title in (("C2", manufactured, "Дата изготовления"),
                                   ("F2", tested, "Дата испытания")):
        if str(raw(book, address) or "").strip() and parsed is None:
            messages.append(f"{title} не разобрана; исходная запись "
                            f"{address}: {raw(book, address)}.")
    if manufactured and tested and tested < manufactured:
        messages.append("Дата испытания раньше даты изготовления: "
                        "так записано в источнике.")
    if any(marker in book["sheet"].casefold() for marker in ("ст.", "стар", "ст ")):
        messages.append("Пометки выдержки сохранены дословно; "
                        "сроки и температуры не достроены.")
    return "\n".join(messages)


def extract_test(book, series_title):
    labels = row_labels(book)
    output = runs(book)
    identifier_cell = "B3" if str(raw(book, "B3") or "").strip() else "B4"
    identifier = str(raw(book, identifier_cell) or book["sheet"])
    manufactured, tested = date_value(book, "C2"), date_value(book, "F2")
    consumed = set()
    for row, label in labels.items():
        scope = row_scope(row, output, labels)
        append_row_result(book, row, label, scope, output, consumed)
    append_shrinkage(book, output, consumed)
    append_mass_results(book, output, consumed)
    add_observations(book, labels, output, consumed)
    return dict(identifier=identifier, identifier_cell=identifier_cell,
                title=f"{series_title} — испытание {book['sheet'].strip()}",
                recorded_manufactured_on=manufactured, recorded_tested_on=tested,
                notes=notes(book, manufactured, tested),
                conditions=extract_conditions(book, labels),
                runs=[run for run in output.values()
                      if run["results"] or run["observations"]])
