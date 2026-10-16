# Data importers — useful first example

This application summarizes real CSV and JSON order files using two
independent versioned plugins. The host picks an importer by extension and
expects every implementation to support `load(path)` returning order rows.

## Run

From the checkout root after `python -m pip install .`:

```bash
python examples/data_importers/run.py
```

```text
orders.csv: 2 items, total $30.25 via example:orders-csv
orders.json: 2 items, total $35.50 via example:orders-json
```

Try adding the separately discovered TSV importer first, with **no host edits**:

```bash
python examples/data_importers/run.py --plugin-dir examples/data_importers/addons examples/data_importers/samples/orders.tsv
```

```text
orders.tsv: 1 item, total $15.00 via example:orders-tsv
```

The ready-to-run add-on lives in `addons/tsv/`, outside the standard
`plugins/` discovery root. It is included to show the extension path; it
is not loaded by the default two-format demonstration.

Pass an existing CSV/JSON file to use your own data:

```bash
python examples/data_importers/run.py /path/to/orders.csv
```

Input rows have `item`, `quantity`, and `unit_price` fields. JSON input is
an array of such objects. The host computes totals with `decimal.Decimal`;
plugins only handle parsing.

## Add a new format without editing the host

The application chooses the ID `example:orders-<extension>`. To add support
for TSV, create a **separate** plugin folder with `manifest.json` declaring
`example:orders-tsv` and a class exposing the same `load(path)` interface.
Pass that folder's parent as an additional discovery root:

```bash
python examples/data_importers/run.py --plugin-dir /path/to/plugins /path/to/orders.tsv
```

[The regression test](../../tests/core/test_data_importers_example.py) creates
an external TSV plugin in a temporary directory to verify that the host
needs no changes. The host-defined interface and ID convention are **not**
automatic compatibility or live hot-swap features of Invariant.

Invariant discovers versioned plugins, selects a requested ID, manages
activation, and closes resources. The application chooses the format and
calculates results. These plugins use **ordinary, trusted Python file reads**:
they do not request governed capabilities or sandbox untrusted code.

For explicit dependencies and governed filesystem permissions, run
[Capability Notes](../capability_notes/README.md). For the fewest concepts,
see [Hello Plugin](../hello_plugin/README.md).
