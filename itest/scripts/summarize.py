import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    rows = []
    for run_dir in sorted(RUNS.glob("*_S*")):
        rp = run_dir / "report.json"
        if not rp.exists():
            continue
        data = json.loads(rp.read_text(encoding="utf-8"))
        scen = (data.get("scenario") or {}).get("id")
        steps = data.get("steps") or []
        failed_step = next((s for s in steps if int(s.get("rc") or 0) != 0), None)
        rows.append(
            {
                "run_id": data.get("run_id"),
                "scenario": scen,
                "ok": bool(data.get("ok")),
                "seconds": sum((s.get("seconds") or 0) for s in steps),
                "counts_before": data.get("counts_before") or {},
                "counts_after": data.get("counts_after") or {},
                "error": data.get("error"),
                "failed_step": (failed_step or {}).get("name"),
                "failed_step_rc": (failed_step or {}).get("rc"),
            }
        )

    rows.sort(key=lambda r: r.get("run_id") or "")

    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_runs": len(rows),
        "ok_runs": sum(1 for r in rows if r["ok"]),
        "failed_runs": sum(1 for r in rows if not r["ok"]),
        "latest_run_id": (rows[-1]["run_id"] if rows else None),
        "by_scenario": {},
        "failures": [],
        "runs": rows,
    }

    by = {}
    for r in rows:
        by.setdefault(r["scenario"], []).append(r)
    for scen, items in sorted(by.items()):
        last = items[-1] if items else None
        summary["by_scenario"][scen] = {
            "runs": len(items),
            "ok": sum(1 for x in items if x["ok"]),
            "failed": sum(1 for x in items if not x["ok"]),
            "ok_rate": (sum(1 for x in items if x["ok"]) / len(items)) if items else 0.0,
            "avg_seconds": (sum(x["seconds"] for x in items) / len(items)) if items else 0.0,
            "latest_run_id": (last or {}).get("run_id"),
            "latest_ok": bool((last or {}).get("ok")) if last else None,
        }

    for r in rows:
        if r["ok"]:
            continue
        summary["failures"].append(
            {
                "run_id": r["run_id"],
                "scenario": r["scenario"],
                "error": r.get("error"),
                "failed_step": r.get("failed_step"),
                "failed_step_rc": r.get("failed_step_rc"),
            }
        )

    (REPORTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    md = []
    md.append("# itest summary\n\n")
    md.append(f"Generated: {summary['generated']}\n\n")
    md.append(
        f"Total runs: {summary['total_runs']} (ok: {summary['ok_runs']}, failed: {summary['failed_runs']})\n\n"
    )
    if summary["latest_run_id"]:
        md.append(f"Latest run: {summary['latest_run_id']}\n\n")
    md.append("## By scenario\n")
    for scen, s in summary["by_scenario"].items():
        latest_state = "ok" if s["latest_ok"] else "failed"
        md.append(
            f"- {scen}: {s['ok']}/{s['runs']} ok ({s['ok_rate']*100:.0f}%), "
            f"avg {s['avg_seconds']:.1f}s, latest {latest_state} ({s['latest_run_id']})\n"
        )
    md.append("\n")
    if summary["failures"]:
        md.append("## Failed runs\n")
        for f in summary["failures"]:
            line = f"- {f['run_id']} ({f['scenario']})"
            if f.get("failed_step"):
                line += f": step `{f['failed_step']}` rc={f['failed_step_rc']}"
            if f.get("error"):
                err = str(f["error"]).strip().splitlines()[0]
                line += f" | {err}"
            md.append(line + "\n")
        md.append("\n")

    (REPORTS / "summary.md").write_text("".join(md), encoding="utf-8")
    print("WROTE", REPORTS / "summary.json")
    print("WROTE", REPORTS / "summary.md")


if __name__ == "__main__":
    main()
