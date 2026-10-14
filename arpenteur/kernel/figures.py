"""A figure as a person types it.

`1'234.50`, `1 234,5` and `12` are all amounts; `abc`, an empty box and `NaN`
are not. A comma alone is the decimal separator; beside a point it is
grouping. Here rather than in the sales module because a project budget and a
quote line are typed the same way, and the clients module may not reach into
sales for it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def parse_decimal(value: object) -> Decimal:
    """The figure as a Decimal, or a ValueError naming what was typed."""
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):
        raise ValueError(f"{value!r} is not an amount")
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827.
        result = Decimal(str(value))
    else:
        text = str(value if value is not None else "").strip()
        for grouping in ("'", "\u2019", " ", "\u00a0"):
            text = text.replace(grouping, "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        try:
            result = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"{value!r} is not an amount") from None
    if not result.is_finite():
        raise ValueError(f"{value!r} is not an amount")
    return result
