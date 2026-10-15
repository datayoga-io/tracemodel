"""Time dimensions whose labels know their dates, after pandas' Period and PeriodIndex.

    m.add_dimension("month", tm.period_range("2026-01", "2027-12", freq="M"))      # "2026-01" ...
    m.add_dimension("period", tm.period_range("2024", "2025", freq="Y-SEP", label="FY{year}"))

A label stays a string, so lag, sel, checks, saving and Excel work as for any dimension; the
dimension also knows each label's Period: its start and end dates, as pandas' start_time and
end_time. In a function formula, x.period is the cell's Period:

    x["start"] in x.period                      # the date falls in this period
    x.period.overlap(x["start"], x["end"])      # the share of the period's days in a date range

Frequencies are pandas' period aliases: "M"; "Q" or "Q-SEP" (quarters of a fiscal year ending
in September); "Y" or "Y-SEP" (fiscal years). A fiscal year is named by the calendar year it
ends in, as in pandas: with "Y-SEP", "2025" is October 2024 to September 2025.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from .errors import DefinitionError

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_STEP = {"M": 1, "Q": 3, "Y": 12}
_DEFAULT_LABEL = {"M": "{year}-{month:02d}", "Q": "{year}Q{quarter}", "Y": "{year}"}


def _parse_freq(freq: str) -> tuple[str, int]:
    """(kind, the month a year ends in, 1-12): "Q-SEP" -> ("Q", 9); "M" -> ("M", 12)."""
    if not isinstance(freq, str):
        raise DefinitionError(f"freq must be a string such as 'M', 'Q-SEP' or 'Y-SEP', got {freq!r}")
    m = re.fullmatch(r"(M|Q|Y|A)(?:-([A-Z]{3}))?", freq.upper())
    if not m or (m[1] == "M" and m[2]) or (m[2] and m[2] not in _MONTHS):
        raise DefinitionError(
            f"freq {freq!r}: use 'M' (months), 'Q' or 'Q-<month>' (quarters), 'Y' or 'Y-<month>' "
            "(years ending in that month), e.g. 'Y-SEP', as pandas names them"
        )
    kind = "Y" if m[1] == "A" else m[1]
    return kind, _MONTHS.index(m[2]) + 1 if m[2] else 12


def _canonical(kind: str, end_month: int) -> str:
    return kind if kind == "M" or end_month == 12 else f"{kind}-{_MONTHS[end_month - 1]}"


class Period:
    """One month, quarter or (fiscal) year, as pandas' Period: Period("2025", "Y-SEP")."""

    __slots__ = ("_end", "_end_month", "_kind", "_label")

    def __init__(self, value: str | date, freq: str = "M", label: str | None = None):
        kind, end_month = _parse_freq(freq)
        self._kind, self._end_month = kind, end_month
        self._end = _end_index(value, kind, end_month)  # the period's last month, as year * 12 + month - 1
        self._label = label

    @classmethod
    def _at(cls, end: int, kind: str, end_month: int, label: str | None) -> Period:
        p = cls.__new__(cls)
        p._end, p._kind, p._end_month, p._label = end, kind, end_month, label
        return p

    # -- what it is ---------------------------------------------------------
    @property
    def freq(self) -> str:
        return _canonical(self._kind, self._end_month)

    @property
    def start_time(self) -> date:
        """The first day, as pandas' start_time (here a date)."""
        first = self._end - _STEP[self._kind] + 1
        return date(first // 12, first % 12 + 1, 1)

    @property
    def end_time(self) -> date:
        """The last day, as pandas' end_time (here a date: the period includes all of it)."""
        y, m = self._end // 12, self._end % 12 + 1
        return date(y, m, calendar.monthrange(y, m)[1])

    @property
    def days(self) -> int:
        """The number of days in the period."""
        return (self.end_time - self.start_time).days + 1

    @property
    def year(self) -> int:
        """The year it is named by: a month's calendar year, a fiscal quarter's or year's
        fiscal year (the year it ends in), as pandas' qyear."""
        if self._kind == "M":
            return self._end // 12
        return _fiscal_year_end(self._end, self._end_month) // 12

    @property
    def quarter(self) -> int:
        """The (fiscal) quarter, 1-4; a year is its own 4th quarter's."""
        fy_end = _fiscal_year_end(self._end, self._end_month)
        return 4 - (fy_end - self._end) // 3

    @property
    def month(self) -> int:
        """The calendar month it ends in."""
        return self._end % 12 + 1

    @property
    def label(self) -> str:
        """As the dimension writes it: pandas' form ("2026-03", "2026Q1", "2026") or its label template."""
        return (self._label or _DEFAULT_LABEL[self._kind]).format(year=self.year, quarter=self.quarter, month=self.month)

    def asfreq(self, freq: str) -> Period:
        """The period of `freq` that holds this one's end, as pandas' asfreq(freq, how="end")."""
        return Period(self.end_time, freq)

    def overlap(self, start: Any = None, end: Any = None) -> float:
        """The share of the period's days from `start` to `end`, both included; None is open.
        Prorates a fee that starts mid-month, or spreads an amount evenly over a date range:
        amount * p.overlap(s, e) / (days from s to e) per period."""
        lo = max(self.start_time, _date(start)) if start is not None else self.start_time
        hi = min(self.end_time, _date(end)) if end is not None else self.end_time
        return max(0, (hi - lo).days + 1) / self.days

    def shift(self, n: int) -> Period:
        return Period._at(self._end + n * _STEP[self._kind], self._kind, self._end_month, self._label)

    # -- comparing ----------------------------------------------------------
    def __contains__(self, value: Any) -> bool:
        """`d in p`: the date falls in the period."""
        return self.start_time <= _date(value) <= self.end_time

    def _key(self, other: Any) -> tuple[date, date]:
        """(start, end) of a Period, or of a date as a single day."""
        if isinstance(other, Period):
            return other.start_time, other.end_time
        d = _date(other)
        return d, d

    def __lt__(self, other: Any) -> bool:  # wholly before
        return self.end_time < self._key(other)[0]

    def __gt__(self, other: Any) -> bool:  # wholly after
        return self.start_time > self._key(other)[1]

    def __le__(self, other: Any) -> bool:
        return self.start_time <= self._key(other)[0] if isinstance(other, Period) else self.start_time <= _date(other)

    def __ge__(self, other: Any) -> bool:
        return self.end_time >= self._key(other)[1] if isinstance(other, Period) else self.end_time >= _date(other)

    def __eq__(self, other: object) -> bool:
        """Equal to the same period, or to its label."""
        if isinstance(other, Period):
            return (self._end, self._kind, self._end_month) == (other._end, other._kind, other._end_month)
        if isinstance(other, str):
            return other == self.label
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._end, self._kind, self._end_month))

    def __str__(self) -> str:
        return self.label

    def __repr__(self) -> str:
        return f"Period({self.label!r}, freq={self.freq!r}: {self.start_time} to {self.end_time})"


class PeriodRange:
    """Consecutive periods, the labels of a time dimension: tm.period_range(...)."""

    def __init__(self, first: Period, n: int):
        if n < 1:
            raise DefinitionError("a period range needs at least one period")
        self.periods: tuple[Period, ...] = tuple(first.shift(i) for i in range(n))
        self.labels: tuple[str, ...] = tuple(p.label for p in self.periods)
        if len(set(self.labels)) != n:
            raise DefinitionError(f"label {first._label!r} gives two periods the same label; include {{year}}")
        self._by_label = dict(zip(self.labels, self.periods))

    @property
    def freq(self) -> str:
        return self.periods[0].freq

    @property
    def label(self) -> str | None:
        return self.periods[0]._label

    def period(self, label: str) -> Period:
        return self._by_label[label]

    def extended(self, more: PeriodRange) -> PeriodRange:
        """These periods followed by `more`, which must continue them."""
        last = self.periods[-1]
        if (more.freq, more.label) != (self.freq, self.label):
            raise DefinitionError(
                f"can't add {more.freq!r} periods labelled {more.label!r} to {self.freq!r} periods labelled {self.label!r}")
        overlap = [p for p in more.periods if p.label in self._by_label]
        new = [p for p in more.periods if p.label not in self._by_label]
        if not new:
            return self
        if new[0] != last.shift(1) or (overlap and overlap[-1] != last):
            raise DefinitionError(f"time periods must continue the dimension: after {last.label} comes "
                                  f"{last.shift(1).label}, not {new[0].label}")
        return PeriodRange(self.periods[0], len(self.periods) + len(new))

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"freq": self.freq, "first": _pandas_label(self.periods[0])}
        if self.label is not None:
            data["label"] = self.label
        return data

    @classmethod
    def from_data(cls, data: dict[str, Any], n: int) -> PeriodRange:
        return cls(Period(data["first"], data["freq"], data.get("label")), n)

    def __iter__(self) -> Iterator[str]:
        return iter(self.labels)

    def __len__(self) -> int:
        return len(self.periods)

    def __repr__(self) -> str:
        return f"PeriodRange({self.labels[0]!r}..{self.labels[-1]!r}, freq={self.freq!r}, {len(self)} periods)"


def period_range(start: str | date | None = None, end: str | date | None = None, periods: int | None = None,
                 freq: str = "M", label: str | None = None) -> PeriodRange:
    """Consecutive periods for a time dimension, as pandas' period_range: two of start, end and
    periods. start and end are dates or pandas' period strings ("2026-03", "2026Q1", "2026").
    `label` formats the labels, e.g. "FY{year}"; fields: year, quarter, month.
    """
    if sum(x is not None for x in (start, end, periods)) != 2:
        raise DefinitionError("period_range: give two of start, end and periods")
    if label is not None:
        try:
            label.format(year=2026, quarter=1, month=1)
        except (KeyError, IndexError, ValueError) as e:
            raise DefinitionError(f"label {label!r}: use the fields {{year}}, {{quarter}} and {{month}} ({e})") from None
    if start is not None:
        first = Period(start, freq, label)
        if end is not None:
            last = Period(end, freq, label)
            if last._end < first._end:
                raise DefinitionError(f"period_range: end {last.label} is before start {first.label}")
            periods = (last._end - first._end) // _STEP[first._kind] + 1
    else:
        first = Period(end, freq, label).shift(1 - periods)  # type: ignore[arg-type, operator]
    if not isinstance(periods, int) or isinstance(periods, bool) or periods < 1:
        raise DefinitionError(f"period_range: periods must be a positive int, got {periods!r}")
    return PeriodRange(first, periods)


# == helpers =================================================================

def _end_index(value: str | date, kind: str, end_month: int) -> int:
    """The last month (year * 12 + month - 1) of the `kind` period that `value` names or holds."""
    if isinstance(value, Period):
        value = value.end_time
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return _containing(value.year * 12 + value.month - 1, kind, end_month)
    if not isinstance(value, str):
        raise DefinitionError(f"a period is a date or a string such as '2026-03', got {value!r}")
    s = value.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", s):
        month = int(m[2])
        if not 1 <= month <= 12:
            raise DefinitionError(f"period {value!r}: no month {month}")
        return _containing(int(m[1]) * 12 + month - 1, kind, end_month)
    if (m := re.fullmatch(r"(\d{4})Q([1-4])", s)) and kind == "Q":
        return int(m[1]) * 12 + end_month - 1 - (4 - int(m[2])) * 3
    if (m := re.fullmatch(r"\d{4}", s)) and kind == "Y":
        return int(s) * 12 + end_month - 1
    examples = {"M": "'2026-03'", "Q": "'2026Q1' or '2026-03'", "Y": "'2026' or '2026-03'"}[kind]
    raise DefinitionError(f"period {value!r}: for this freq write {examples}, or a date")


def _containing(month: int, kind: str, end_month: int) -> int:
    """The last month of the `kind` period holding `month`."""
    step = _STEP[kind]
    return month + (end_month - 1 - month % 12) % step


def _fiscal_year_end(month: int, end_month: int) -> int:
    return month + (end_month - 1 - month % 12) % 12


def _pandas_label(p: Period) -> str:
    return _DEFAULT_LABEL[p._kind].format(year=p.year, quarter=p.quarter, month=p.month)


def _date(value: Any) -> date:
    """A date from a date, a datetime (its day), an ISO string, or a cell in a function formula."""
    get = getattr(value, "_get", None)
    if get is not None:
        value = get()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    raise DefinitionError(f"expected a date, got {value!r}")


__all__ = ["Period", "PeriodRange", "period_range"]
