"""Table.to_pandas: a DataFrame from the table's columns and rows; a clear ImportError without pandas."""

import sys

import pytest

import tracemodel as tm


def test_missing_pandas_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", None)  # makes `import pandas` fail
    t = tm.Table(["period", "value"], [["FY2024", 1]])
    with pytest.raises(ImportError, match=r"pytracemodel\[pandas\]"):
        t.to_pandas()


def test_to_pandas_frame():
    pd = pytest.importorskip("pandas")
    t = tm.Table(["period", "segment", "value"], [["FY2024", "/a", 1.5], ["FY2025", "/a", 2.0]])
    df = t.to_pandas()
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["period", "segment", "value"]
    assert df["value"].tolist() == [1.5, 2.0]
    assert len(df) == len(t)
