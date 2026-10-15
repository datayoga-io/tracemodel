"""Apple FY2024-FY2025 income statement: the step 1 example from the requirements doc.

Run it:              uv run python examples/apple.py
Explore it:          uv run jupyter lab apple.ipynb
"""

import tracemodel as tm

TENK = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
SO = {"document": "Apple 10-K FY2025", "url": TENK,
      "section": "Item 8, Consolidated Statements of Operations"}
CAT = {"document": "Apple 10-K FY2025", "url": TENK,
       "section": "Item 8, Note 2 – Revenue, net sales by category"}
GM = {"document": "Apple 10-K FY2025", "url": TENK,
      "section": "Item 7, MD&A, Gross Margin table"}

USD_M = {"units": "USD", "scale": "millions", "period_type": "duration"}  # flows over the year


def build_model() -> tm.Model:
    """Definition agent: steps 1-3."""
    model = tm.Model()

    # 1. Dimensions
    model.add_dimension("period", [
        ("FY2024", "Fiscal 2024", {"end": "2024-09-28"}),
        ("FY2025", "Fiscal 2025", {"end": "2025-09-27"}),
    ])
    model.add_dimension("sales_category", tm.Hierarchy([
        ("/", "Total"),
        "/products",
        "/products/iphone",
        "/products/mac",
        "/products/ipad",
        ("/products/wearables", "Wearables, Home and Accessories"),
        "/services",
    ]))
    model.add_dimension("expense_category", tm.Hierarchy([
        ("/rnd", "Research and development"),
        ("/gna", "Selling, general and administrative"),
    ]))

    # 2. Items scaffold
    model.add_item("net_sales", dims=("sales_category", "period"), dtype=float, attrs=USD_M)
    model.add_item("cost_of_sales", dims=("sales_category", "period"), dtype=float, attrs=USD_M)
    model.add_item("gross_margin", dims=("sales_category", "period"), dtype=float, attrs=USD_M)
    model.add_item("operating_expense", dims=("expense_category", "period"), dtype=float, attrs=USD_M)
    model.add_item("shares_basic", dims=("period",), dtype=int, attrs={"units": "shares", "scale": "thousands", "period_type": "duration"})
    model.add_item("earnings_per_share", dims=("period",), dtype=float, attrs={"units": "USD/share", "period_type": "duration"})

    # 3. Data quality
    model.add_check("DQ-1",
        coords={"period": tm.ANY},
        expect=lambda x: abs(
            x["net_sales"]["/"]
            - (x["net_sales"]["/products"] + x["net_sales"]["/services"])
        ) <= 0.2,
    )
    return model


def enter_values(model: tm.Model) -> tm.Model:
    """Values agent: step 4. Only set_value calls."""
    # net_sales: sales_category x period
    model.set_value("net_sales", coords={"sales_category": "/", "period": "FY2024"}, value=391_035, source=SO)
    model.set_value("net_sales", coords={"sales_category": "/", "period": "FY2025"}, value=416_161, source=SO)
    model.set_value("net_sales", coords={"sales_category": "/products", "period": "FY2024"}, value=294_866, source=SO)
    model.set_value("net_sales", coords={"sales_category": "/products", "period": "FY2025"}, value=307_003, source=SO)
    model.set_value("net_sales", coords={"sales_category": "/services", "period": "FY2024"}, value=96_169, source=SO)
    model.set_value("net_sales", coords={"sales_category": "/services", "period": "FY2025"}, value=109_158, source=SO)
    model.set_value("net_sales", coords={"sales_category": "/products/iphone", "period": "FY2024"}, value=201_183, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/iphone", "period": "FY2025"}, value=209_586, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/mac", "period": "FY2024"}, value=29_984, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/mac", "period": "FY2025"}, value=33_708, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/ipad", "period": "FY2024"}, value=26_694, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/ipad", "period": "FY2025"}, value=28_023, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/wearables", "period": "FY2024"}, value=37_005, source=CAT)
    model.set_value("net_sales", coords={"sales_category": "/products/wearables", "period": "FY2025"}, value=35_686, source=CAT)

    # cost_of_sales: sales_category x period (Apple reports /, /products, /services only)
    model.set_value("cost_of_sales", coords={"sales_category": "/", "period": "FY2024"}, value=210_352, source=SO)
    model.set_value("cost_of_sales", coords={"sales_category": "/", "period": "FY2025"}, value=220_960, source=SO)
    model.set_value("cost_of_sales", coords={"sales_category": "/products", "period": "FY2024"}, value=185_233, source=SO)
    model.set_value("cost_of_sales", coords={"sales_category": "/products", "period": "FY2025"}, value=194_116, source=SO)
    model.set_value("cost_of_sales", coords={"sales_category": "/services", "period": "FY2024"}, value=25_119, source=SO)
    model.set_value("cost_of_sales", coords={"sales_category": "/services", "period": "FY2025"}, value=26_844, source=SO)

    # gross_margin: sales_category x period
    model.set_value("gross_margin", coords={"sales_category": "/", "period": "FY2024"}, value=180_683, source=SO)
    model.set_value("gross_margin", coords={"sales_category": "/", "period": "FY2025"}, value=195_201, source=SO)
    model.set_value("gross_margin", coords={"sales_category": "/products", "period": "FY2024"}, value=109_633, source=GM)
    model.set_value("gross_margin", coords={"sales_category": "/products", "period": "FY2025"}, value=112_887, source=GM)
    model.set_value("gross_margin", coords={"sales_category": "/services", "period": "FY2024"}, value=71_050, source=GM)
    model.set_value("gross_margin", coords={"sales_category": "/services", "period": "FY2025"}, value=82_314, source=GM)

    # operating_expense: expense_category x period
    model.set_value("operating_expense", coords={"expense_category": "/rnd", "period": "FY2024"}, value=31_370, source=SO)
    model.set_value("operating_expense", coords={"expense_category": "/rnd", "period": "FY2025"}, value=34_550, source=SO)
    model.set_value("operating_expense", coords={"expense_category": "/gna", "period": "FY2024"}, value=26_097, source=SO)
    model.set_value("operating_expense", coords={"expense_category": "/gna", "period": "FY2025"}, value=27_601, source=SO)

    # shares_basic and earnings_per_share: period only
    model.set_value("shares_basic", coords={"period": "FY2024"}, value=15_343_783, source=SO)
    model.set_value("shares_basic", coords={"period": "FY2025"}, value=14_948_500, source=SO)
    model.set_value("earnings_per_share", coords={"period": "FY2024"}, value=6.11, source=SO)
    model.set_value("earnings_per_share", coords={"period": "FY2025"}, value=7.49, source=SO)

    return model


model = enter_values(build_model())

if __name__ == "__main__":
    print(model, end="\n\n")
    print(model.table("net_sales"), end="\n\n")
    print(model.check())
