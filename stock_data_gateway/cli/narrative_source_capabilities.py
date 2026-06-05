from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from stock_data_gateway.source_event_capabilities import narrative_source_capabilities

DEFAULT_OUTPUT_DIR = Path("outputs/narrative_source_capabilities/current")


@dataclass(frozen=True)
class NarrativeSourceCapabilitiesRequest:
    output_dir: Path
    acceptance_json: Path | None = None


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_narrative_source_capabilities(
        NarrativeSourceCapabilitiesRequest(
            output_dir=args.output_dir,
            acceptance_json=args.acceptance_json,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def run_narrative_source_capabilities(request: NarrativeSourceCapabilitiesRequest) -> dict[str, Any]:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    acceptance_status = _read_acceptance_status(request.acceptance_json)
    report = {
        "ok": True,
        "status": "ok",
        "generated_at": _utc_now(),
        "acceptance_json": str(request.acceptance_json) if request.acceptance_json else None,
        "capabilities": narrative_source_capabilities(acceptance_status),
        "artifacts": {
            "json": str(request.output_dir / "narrative_source_capabilities.json"),
            "html": str(request.output_dir / "narrative_source_capabilities.html"),
        },
    }
    _write_report(request.output_dir, report)
    return {
        "ok": True,
        "status": "ok",
        "output_dir": str(request.output_dir),
        "json": report["artifacts"]["json"],
        "html": report["artifacts"]["html"],
    }


def _read_acceptance_status(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    checks = payload.get("source_checks")
    if not isinstance(checks, list):
        return {}
    statuses = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        source_kind = str(check.get("source_kind") or "")
        status = str(check.get("status") or "")
        if source_kind and status:
            statuses[source_kind] = status
    return statuses


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "narrative_source_capabilities.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "narrative_source_capabilities.html").write_text(_html_report(report), encoding="utf-8")


def _html_report(report: dict[str, Any]) -> str:
    rows = "\n".join(_html_row(row) for row in report["capabilities"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>叙事源能力清单</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172026; }}
    h1 {{ font-size: 26px; margin-bottom: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 13px; }}
    th, td {{ border: 1px solid #d7dee5; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f7f9; }}
  </style>
</head>
<body>
  <h1>叙事源能力清单</h1>
  <div>生成时间：{_e(report["generated_at"])}</div>
  <table>
    <thead>
      <tr>
        <th>source_id</th><th>provider</th><th>route</th><th>status</th><th>enabled_by_default</th>
        <th>credential_required</th><th>permission_probe_available</th><th>trust_tier</th>
        <th>license_scope</th><th>retention_policy</th><th>cache_policy</th>
        <th>last_acceptance_status</th><th>known_limitations</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""


def _html_row(row: dict[str, Any]) -> str:
    return f"""<tr>
  <td>{_e(row.get("source_id"))}</td>
  <td>{_e(row.get("provider"))}</td>
  <td>{_e(row.get("route"))}</td>
  <td>{_e(row.get("status"))}</td>
  <td>{_yes_no(bool(row.get("enabled_by_default")))}</td>
  <td>{_yes_no(bool(row.get("credential_required")))}</td>
  <td>{_yes_no(bool(row.get("permission_probe_available")))}</td>
  <td>{_e(row.get("trust_tier"))}</td>
  <td>{_e(row.get("license_scope"))}</td>
  <td>{_e(row.get("retention_policy"))}</td>
  <td>{_e(row.get("cache_policy"))}</td>
  <td>{_e(row.get("last_acceptance_status"))}</td>
  <td>{_e(row.get("known_limitations"))}</td>
</tr>"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write gateway narrative source capability JSON/HTML reports.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--acceptance-json", type=Path, default=None)
    return parser


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
