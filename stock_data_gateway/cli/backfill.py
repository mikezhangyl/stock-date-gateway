from __future__ import annotations

import argparse
import json

from stock_data_gateway.main import create_default_gateway


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill cyq_chips through the local gateway.")
    parser.add_argument("--ts-code", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    gateway = create_default_gateway()
    result = gateway.query(
        "tushare",
        "cyq_chips",
        {"ts_code": args.ts_code, "start_date": args.start_date, "end_date": args.end_date},
    )
    print(
        json.dumps(
            {"status": result.meta.get("status"), "rows": len(result.data.items), "meta": result.meta},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
