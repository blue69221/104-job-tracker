# -*- coding: utf-8 -*-
"""看一眼資料庫目前的狀態。用來確認寫入的內容合理，以及日後除錯。"""
import sys
import json
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from .store import Store          # noqa: E402


def count(sb, table, **eq):
    q = sb.table(table).select("*", count="exact").limit(0)
    for k, v in eq.items():
        q = q.is_(k, "null") if v is None else q.eq(k, v)
    return q.execute().count


def main():
    sb = Store().sb

    print("=" * 72)
    print("資料庫現況")
    print("=" * 72)
    total = count(sb, "jobs")
    print(f"  jobs                {total:>6}  （在架 {count(sb, 'jobs', is_open=True)}）")
    print(f"  已抓詳情            {total - count(sb, 'jobs', detail_fetched_at=None):>6}")
    print(f"  job_events          {count(sb, 'job_events'):>6}")
    print(f"  queries             {count(sb, 'queries'):>6}")

    print("-" * 72)
    print("事件分佈")
    for t in ("discovered", "relisted", "reopened", "changed", "closed"):
        print(f"  {t:<12} {count(sb, 'job_events', event_type=t):>6}")

    print("-" * 72)
    print("執行記錄（最近 5 次）")
    runs = (sb.table("runs").select("*")
            .order("started_at", desc=True).limit(5).execute().data)
    for r in runs:
        print(f"  #{r['id']} {r['status']:<8} 頁={r.get('pages')} "
              f"職缺={r.get('jobs_seen')} 新增={r.get('new_jobs')} "
              f"重刊={r.get('relisted')} 下架={r.get('closed')} "
              f"詳情={r.get('details_fetched')}")
        if r.get("error"):
            print(f"      error: {r['error'][:110]}")

    print("-" * 72)
    print("列表欄位抽樣")
    row = (sb.table("jobs").select(
        "job_no,enc_id,job_name,cust_name,cust_no,job_addr_desc,"
        "appear_date,apply_cnt,first_seen,last_seen,dedupe_key,description")
        .limit(1).execute().data)
    if row:
        r = row[0]
        for k in ("job_no", "enc_id", "job_name", "cust_name", "cust_no",
                  "job_addr_desc", "appear_date", "apply_cnt",
                  "first_seen", "last_seen", "dedupe_key"):
            print(f"  {k:<16} {r.get(k)}")
        desc = (r.get("description") or "").replace("\n", " ")
        print(f"  {'description':<16} {len(r.get('description') or '')} 字：{desc[:60]}…")

    print("-" * 72)
    print("詳情頁欄位抽樣（列表沒有這些，只有詳情頁才有）")
    rows = (sb.table("jobs")
            .select("job_no,job_name,salary_text,salary_min,salary_max,work_exp,edu,detail")
            .not_.is_("detail_fetched_at", "null").limit(2).execute().data)
    if not rows:
        print("  （還沒有任何職缺抓過詳情）")
    for r in rows:
        print(f"  ── {r['job_name'][:34]}")
        print(f"     薪資     {r.get('salary_text')}  ({r.get('salary_min')} ~ {r.get('salary_max')})")
        print(f"     經驗     {r.get('work_exp')}")
        print(f"     學歷     {r.get('edu')}")
        d = r.get("detail") or {}
        cond = d.get("condition") or {}
        welfare = d.get("welfare") or {}
        print(f"     技能     {json.dumps(cond.get('specialty') or cond.get('skill'), ensure_ascii=False)[:70]}")
        print(f"     福利標籤 {json.dumps(welfare.get('tag'), ensure_ascii=False)[:70]}")
        print(f"     detail 區塊 {list(d.keys())[:9]}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
