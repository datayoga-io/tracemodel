# Example

A retail chain's sales by region and quarter.

```python
import tracemodel as tm

model = tm.Model()

# 1. Dimensions
model.add_dimension("period", [
    ("Q1", "First quarter", {"end": "2026-03-31"}),   # (code, description, attrs)
    ("Q2", "Second quarter", {"end": "2026-06-30"}),
])
model.add_dimension("region", tm.Hierarchy([
    ("/", "All regions"),                              # (code, description)
    "/north",                                          # code only
    "/north/boston",
    "/north/portland",
    ("/south", "South"),
]))
model.add_dimension("channel", tm.Hierarchy(["/store", "/online"]))

# 2. Items scaffold
USD_K = {"units": "USD", "scale": "thousands"}
model.add_item("revenue", dims=("region", "period"), dtype=float, attrs=USD_K, description="Net revenue")
model.add_item("returns", dims=("channel", "period"), dtype=float, attrs=USD_K, description="Customer returns")
model.add_item("stores", dims=("period",), dtype=int, attrs={"units": "stores"})

# 3. Data quality: total revenue equals north plus south, in every period
model.add_check("DQ-1",
    coords={"period": tm.ANY},
    expect=lambda x: abs(
        x["revenue"]["/"] - (x["revenue"]["/north"] + x["revenue"]["/south"])
    ) <= 0.5,                                  # within 0.5 (USD thousands) for rounding
    description="Total revenue = North + South",
)

# 4. Values
SRC = {"document": "Q2 sales report", "url": "https://example.com/q2.pdf", "section": "Table 1"}
model.set_value("revenue", coords={"region": "/", "period": "Q1"}, value=1_200, source=SRC)
model.set_value("revenue", coords={"region": "/north", "period": "Q1"}, value=700, source=SRC)
model.set_value("revenue", coords={"region": "/south", "period": "Q1"}, value=500, source=SRC)
model.set_value("stores", coords={"period": "Q1"}, value=42, source=SRC)

model.check()            # DQ-1 passes for Q1; Q2 fails because its values are missing
model.table("revenue")   # regions down, periods across
```
