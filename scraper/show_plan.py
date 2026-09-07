# -*- coding: utf-8 -*-
"""印出目前設定會產生的查詢計畫。用來檢查有沒有查詢會被 104 截斷。"""
import sys, logging
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from .client import Client            # noqa: E402
from .planner import build_plan, plan_summary  # noqa: E402


def main():
    client = Client()
    queries, warnings = build_plan(client)
    probe_requests = client.request_count
    queries.sort(key=lambda q: -q.total)

    print(f"\n{'jobcat':<12} {'area':<12} {'total':>6} {'pages':>6}  label")
    print("-" * 92)
    for q in queries:
        mark = "  <-- 會被截斷" if q.truncated else ""
        print(f"{q.jobcat:<12} {q.area:<12} {q.total:>6} {q.pages:>6}  {q.label}{mark}")

    s = plan_summary(queries)
    print("-" * 92)
    print(f"查詢數 {s['queries']} / 總頁數 {s['pages']} / 可取記錄 {s['records']} "
          f"/ 預估 {s['est_minutes']} 分鐘（規劃階段另需 {probe_requests} 次探測請求）")
    if warnings:
        print(f"\n!! {len(warnings)} 個警告：")
        for w in warnings:
            print(f"   - {w}")
    else:
        print("\n沒有查詢會撞到 104 的 3000 筆上限，覆蓋率 100%。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
