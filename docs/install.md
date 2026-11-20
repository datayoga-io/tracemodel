# Install

Python 3.12 or later. One dependency, [RestrictedPython](https://github.com/zopefoundation/RestrictedPython),
which runs formulas safely; the Excel export needs openpyxl.

```bash
pip install pytracemodel
pip install "pytracemodel[excel]"    # with model.to_excel
```

```python
import tracemodel as tm
```
