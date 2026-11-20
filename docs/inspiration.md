# Inspiration

| Project | What we take from it |
| --- | --- |
| [pandas](https://pandas.pydata.org/) | Date and datetime columns, ISO strings parsed as dates, date differences as timedeltas, the `.dt` accessor's field names and `to_period` labels. |
| [xarray](https://docs.xarray.dev/) | Named dimensions, coordinate labels, selection by label (`.sel`), broadcasting by dimension name. |
| [Great Expectations](https://docs.greatexpectations.io/) | Data quality rules as declared *expectations*, grouped into suites and run against the data; `severity` (critical / warning / info) and `mostly` (pass when a share of rows passes). |
| [XBRL Formula](https://www.xbrl.org/specification/validation/REC-2009-06-22/validation-REC-2009-06-22.html) | Value and consistency *assertions* over reported financial facts, with absolute and proportional acceptance radii; satisfied / unsatisfied with a severity. |
