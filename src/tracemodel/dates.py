"""Date and datetime items, after pandas and xarray.

An item with dtype "date" holds `datetime.date` values; "datetime" holds `datetime.datetime`
values without a time zone, as numpy's datetime64 (which xarray uses) has none. Values may be
entered as ISO strings ("2026-03-31"), as pandas' `to_datetime` reads them. A difference of
two dates is a `timedelta`, as in pandas, and `.dt` in a function formula gives the fields
pandas' `Series.dt` does: year, month, quarter, to_period("M"), ...
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from .errors import DefinitionError, InvalidValue

TEMPORAL = (date, datetime)
DTYPE_NAMES: dict[str, type] = {"date": date, "datetime": datetime}
_DATETIME64 = re.compile(r"datetime64(\[\w+\])?")


def dtype_from_name(name: str) -> type | str:
    """"date", "datetime", or numpy's "datetime64[ns]" (a datetime); other names unchanged."""
    if name in DTYPE_NAMES:
        return DTYPE_NAMES[name]
    return datetime if _DATETIME64.fullmatch(name) else name


def kind(value: Any) -> str:
    """number, date, datetime, timedelta or bool: what a value is, for messages and checks."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, timedelta):
        return "timedelta"
    return type(value).__name__


def coerce(dtype: type, value: Any, name: str) -> date | datetime:
    """A value entered for a date or datetime item, as exactly that type.

    ISO strings are parsed; a pandas Timestamp or numpy datetime64 becomes a plain datetime; a
    date entered for a datetime item is midnight; a datetime entered for a date item must be
    midnight.
    """
    if type(value).__name__ == "datetime64":  # numpy, without importing it
        value = value.astype("datetime64[us]").item()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
        except ValueError:
            raise InvalidValue(f"{name}: {value!r} is not an ISO date, e.g. '2026-03-31' or '2026-03-31T09:30'") from None
    if isinstance(value, datetime) and value.tzinfo is not None:
        raise InvalidValue(
            f"{name}: {value!r} has a time zone; dates and datetimes have none (as numpy's datetime64). "
            "Convert it first, e.g. value.astimezone(UTC).replace(tzinfo=None)"
        )
    if dtype is date:
        if isinstance(value, datetime):
            if value.time() != time():
                raise InvalidValue(f"{name} is a date; {value!r} has a time of day. Use value.date(), or make the item a datetime")
            return date(value.year, value.month, value.day)
        if isinstance(value, date):
            return date(value.year, value.month, value.day)
        raise InvalidValue(f"{name} is a date; got {value!r}. Enter a datetime.date or an ISO string, e.g. '2026-03-31'")
    if isinstance(value, datetime):  # a pandas Timestamp becomes a plain datetime
        return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second, value.microsecond)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raise InvalidValue(f"{name} is a datetime; got {value!r}. Enter a datetime.datetime or an ISO string, e.g. '2026-03-31T09:30'")


def literal(value: date | timedelta) -> str:
    """How an expression shows a date constant; text.parse reads it back."""
    if isinstance(value, datetime):
        return f"datetime({value.isoformat()!r})"
    if isinstance(value, date):
        return f"date({value.isoformat()!r})"
    parts = {"days": value.days, "seconds": value.seconds, "microseconds": value.microseconds}
    shown = ", ".join(f"{k}={v}" for k, v in parts.items() if v) or "days=0"
    return f"timedelta({shown})"


def to_json(value: Any) -> Any:
    """A value for a JSON file: dates and datetimes as ISO strings."""
    return value.isoformat() if isinstance(value, date) else value


def fmt(value: date | timedelta) -> str:
    """As a table shows it: ISO dates; a timedelta in whole days as "30 days", as pandas does."""
    if isinstance(value, timedelta):
        if value.seconds or value.microseconds:
            return str(value)
        return f"{value.days} day{'s' * (abs(value.days) != 1)}"
    return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()


# == saved function formulas: dates in the names a formula uses =================

def env_value(value: Any) -> dict[str, Any] | None:
    """A date, datetime, timedelta or one of those classes as JSON; None for anything else."""
    if any(value is c for c in (date, datetime, timedelta)):
        return {"$class": value.__name__}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, timedelta):
        return {"$timedelta": [value.days, value.seconds, value.microseconds]}
    return None


def env_restore(key: str, v: Any) -> Any:
    if key == "$class":
        return {"date": date, "datetime": datetime, "timedelta": timedelta}[v]
    if key == "$datetime":
        return datetime.fromisoformat(v)
    if key == "$date":
        return date.fromisoformat(v)
    if key == "$timedelta":
        return timedelta(*v)
    raise KeyError(key)


ENV_KEYS = ("$class", "$datetime", "$date", "$timedelta")


# == x["d"].dt in a function formula ============================================

_PERIODS = {
    "D": lambda d: d.strftime("%Y-%m-%d"),
    "M": lambda d: d.strftime("%Y-%m"),
    "Q": lambda d: f"{d.year}Q{(d.month - 1) // 3 + 1}",
    "Y": lambda d: str(d.year),
}


class DatetimeAccessor:
    """`.dt` on a date cell, named as pandas' Series.dt: x["start"].dt.month, .dt.to_period("M")."""

    __slots__ = ("_v",)

    def __init__(self, value: date):
        self._v = value

    year = property(lambda self: self._v.year)
    month = property(lambda self: self._v.month)
    day = property(lambda self: self._v.day)
    hour = property(lambda self: getattr(self._v, "hour", 0))
    minute = property(lambda self: getattr(self._v, "minute", 0))
    second = property(lambda self: getattr(self._v, "second", 0))
    quarter = property(lambda self: (self._v.month - 1) // 3 + 1)
    dayofweek = property(lambda self: self._v.weekday())  # Monday 0, as in pandas
    dayofyear = property(lambda self: self._v.timetuple().tm_yday)
    days_in_month = property(lambda self: calendar.monthrange(self._v.year, self._v.month)[1])
    is_month_start = property(lambda self: self._v.day == 1)
    is_month_end = property(lambda self: self._v.day == self.days_in_month)
    is_quarter_start = property(lambda self: self.is_month_start and self._v.month in (1, 4, 7, 10))
    is_quarter_end = property(lambda self: self.is_month_end and self._v.month in (3, 6, 9, 12))
    is_year_start = property(lambda self: (self._v.month, self._v.day) == (1, 1))
    is_year_end = property(lambda self: (self._v.month, self._v.day) == (12, 31))

    @property
    def date(self) -> date:
        """The date part (of a datetime)."""
        return date(self._v.year, self._v.month, self._v.day)

    def strftime(self, fmt: str) -> str:
        return self._v.strftime(fmt)

    def to_period(self, freq: str) -> str:
        """The period label holding this date, as pandas prints a Period: "M" -> "2026-03",
        "Q" -> "2026Q1", "Y" -> "2026", "D" -> "2026-03-31". It matches a dim's labels when
        they are written that way, e.g. x["start"].dt.to_period("M") == x.coords["month"]."""
        if freq not in _PERIODS:
            raise DefinitionError(f"to_period({freq!r}): freq must be one of {', '.join(map(repr, _PERIODS))}")
        return _PERIODS[freq](self._v)

    def __repr__(self) -> str:
        return f"<dt {self._v.isoformat()}>"
