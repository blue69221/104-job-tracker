# -*- coding: utf-8 -*-
"""每日主流程：掃描列表 -> 判定事件 -> 補抓詳情 -> 推播。

安全規則（寫錯代價很高，特別標出）：
  1. 只有在「所有查詢都完整跑完」時才判定下架。任何一個查詢失敗都
     不做 closed，否則一次網路抖動就會產生上千筆假的下架事件。
  2. 被擋（BlockedError）必須立刻中止並告警，不能當成空結果續跑。
  3. 回鍋職缺（DB 裡已存在但曾下架）不可覆蓋原本的 first_seen。
事件判定本身在 scraper/diff.py，是純函式，有測試。
"""
import sys
import time
import logging
import argparse
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
# httpx 對每一次 Supabase 請求都會印一行完整網址，全量執行會有上千行，
# 把真正該看的訊息淹掉。
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)

from . import notify                                    # noqa: E402
from .client import Client, BlockedError, FetchError    # noqa: E402
from .diff import classify                              # noqa: E402
from .planner import build_plan, plan_summary           # noqa: E402
from .parse import parse_list_job, parse_detail         # noqa: E402
from .store import Store                                # noqa: E402

log = logging.getLogger("run_daily")


def scan_lists(client, queries):
    """跑完整個查詢計畫。回傳 (seen, pages, complete, failures)。"""
    seen, pages, failures = {}, 0, []
    today = date.today()

    for idx, q in enumerate(queries, 1):
        log.info("[%s/%s] %s (%s 筆 / %s 頁)", idx, len(queries), q.label, q.total, q.pages)
        for page in range(1, q.pages + 1):
            try:
                payload = client.search(q.jobcat, q.area, page=page)
            except BlockedError:
                raise                      # 立刻往上拋，中止整輪
            except FetchError as e:
                failures.append("{} p{}: {}".format(q.label, page, e))
                log.error("查詢失敗，本輪將不判定下架: %s", e)
                continue
            pages += 1
            rows = payload.get("data") or []
            for raw in rows:
                parsed = parse_list_job(raw, today)
                if parsed["job_no"]:
                    seen[parsed["job_no"]] = parsed
            if not rows:
                break                      # 提早結束，省請求

    return seen, pages, (not failures), failures


def fetch_details(client, store, targets):
    """抓詳情頁。列表沒有薪資字串、經驗、學歷、技能、福利，只有這裡有。"""
    done = 0
    for row in targets:
        enc_id = row.get("enc_id")
        if not enc_id:
            continue
        try:
            payload = client.detail(enc_id)
        except BlockedError:
            raise
        except FetchError as e:
            log.warning("詳情抓取失敗 %s: %s", row["job_no"], e)
            continue
        if not payload:
            # 404：職缺已被移除。仍蓋上時間戳，否則每天都會重試同一筆。
            store.save_detail(row["job_no"], {"detail_fetched_at": "now()"})
            continue
        fields = parse_detail(payload)
        fields["detail_fetched_at"] = "now()"
        store.save_detail(row["job_no"], fields)
        done += 1
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail-budget", type=int, default=400,
                    help="本次最多抓幾筆詳情頁（首次回填請用 backfill workflow）")
    ap.add_argument("--limit-queries", type=int, default=0,
                    help="只跑前 N 個查詢，用於首次小規模驗證。"
                         "會強制停用下架判定，因為沒掃完全部。")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--web-url", default="")
    args = ap.parse_args()

    today = date.today()
    t0 = time.time()
    client = Client()
    store = Store()
    run_id = store.start_run()
    stats = {"pages": 0, "requests": 0, "jobs_seen": 0, "new_jobs": 0,
             "relisted": 0, "closed": 0, "details_fetched": 0}

    try:
        # ---- 1. 規劃（每次重算，104 調分類或職缺暴增都能自動適應）----
        queries, warnings = build_plan(client)
        partial_run = False
        if args.limit_queries:
            queries = queries[:args.limit_queries]
            partial_run = True             # 沒掃完全部，絕不可判定下架
            log.warning("只跑前 %s 個查詢（驗證模式），本輪不判定下架", len(queries))
        summary = plan_summary(queries)
        log.info("計畫：%s 個查詢 / %s 頁 / 預估 %s 分鐘",
                 summary["queries"], summary["pages"], summary["est_minutes"])
        store.sync_queries([{"jobcat": q.jobcat, "area": q.area, "label": q.label}
                            for q in queries])

        # ---- 2. 掃描 ----
        seen, pages, complete, failures = scan_lists(client, queries)
        complete = complete and not partial_run
        stats["pages"] = pages
        stats["jobs_seen"] = len(seen)
        log.info("掃描完成：%s 頁 / 去重後 %s 筆 / complete=%s", pages, len(seen), complete)

        # ---- 3. 比對（純函式，見 tests/test_diff.py）----
        existing = store.load_open_jobs()
        candidates = set(seen) - set(existing)
        known = store.lookup_jobs(candidates) if candidates else {}
        dedupe_map = store.recent_dedupe_keys() if candidates else {}

        result = classify(seen, existing, known, dedupe_map, today, complete)
        stats["new_jobs"] = len(result.discovered)
        stats["relisted"] = len(result.relisted)
        stats["closed"] = len(result.closed)
        log.info("新增 %s / 重刊 %s / 回鍋 %s / 下架 %s",
                 len(result.discovered), len(result.relisted),
                 len(result.reopened), len(result.closed))

        if not complete:
            log.warning("本輪有 %s 個查詢失敗，已略過下架判定以免產生假的 closed 事件",
                        len(failures))
            warnings = list(warnings) + failures

        # ---- 4. 寫入（new 與 update 欄位集合不同，必須分批）----
        if result.new_rows:
            store.upsert_jobs(result.new_rows)
        if result.update_rows:
            store.upsert_jobs(result.update_rows)
        if result.closed:
            store.mark_closed(result.closed, today)
        if result.events:
            store.insert_events(result.events)

        # ---- 5. 詳情（新職缺優先）----
        if args.detail_budget > 0:
            targets = store.jobs_needing_detail(args.detail_budget)
            stats["details_fetched"] = fetch_details(client, store, targets)
            log.info("詳情補抓 %s 筆", stats["details_fetched"])

        stats["requests"] = client.request_count
        store.finish_run(run_id, "ok", stats, warnings or None)

        # ---- 6. 推播（重刊不列入；已封鎖的公司過濾掉）----
        if not args.no_notify:
            blocked = store.blocked_cust_nos()
            highlights = [seen[j] for j in result.discovered
                          if seen[j].get("cust_no") not in blocked]
            highlights.sort(key=lambda r: (r.get("apply_cnt") or 0))
            notify.send_daily(today, stats, highlights,
                              web_url=args.web_url or None,
                              warnings=warnings or None)

        log.info("完成，耗時 %.1f 分鐘 / %s 次請求 / %s",
                 (time.time() - t0) / 60, client.request_count, stats)
        return 0

    except BlockedError as e:
        stats["requests"] = client.request_count
        store.finish_run(run_id, "blocked", stats, error=e)
        if not args.no_notify:
            notify.send_failure(today, "blocked", e, stats)
        log.error("被 104 擋下，中止：%s", e)
        return 2
    except Exception as e:                 # noqa: BLE001
        stats["requests"] = client.request_count
        store.finish_run(run_id, "failed", stats, error=e)
        if not args.no_notify:
            notify.send_failure(today, "failed", e, stats)
        log.exception("執行失敗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
