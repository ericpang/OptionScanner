"""Dump the raw Longbridge CLI JSON for a symbol, to check/extend the field mapping.

    python -m app.probe AAPL.US
"""
import json
import sys

from .data import CliProvider, DataError

CALLS = [("quote",), ("calc-index", "--fields", "pe,pb,dps_rate,mktcap"), ("company",),
         ("financial-statement", "--kind", "IS", "--report", "af"), ("financial-statement", "--kind", "BS", "--report", "af"),
         ("financial-statement", "--kind", "CF", "--report", "af"), ("financial-statement", "--kind", "IS", "--report", "qf"),
         ("institution-rating",), ("forecast-eps",), ("consensus",), ("news", "--count", "3"), ("insider-trades", "--count", "5"),
         ("shareholder", "--count", "3"), ("capital", "--flow"), ("short-positions", "--count", "2"), ("topic", "--count", "3")]


def main() -> None:
    sym = sys.argv[1] if len(sys.argv) > 1 else "AAPL.US"
    p = CliProvider()
    for c in CALLS:
        args = (c[0], sym, *c[1:])
        print("=" * 80, "\nlongbridge", *args)
        try:
            print(json.dumps(p.run(*args), indent=1, ensure_ascii=False)[:3000])
        except DataError as e:
            print("ERROR:", e)


if __name__ == "__main__":
    main()
