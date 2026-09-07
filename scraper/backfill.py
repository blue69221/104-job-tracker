# -*- coding: utf-8 -*-
"""詳情頁回填：把所有還沒抓過詳情的在架職缺補齊。

首次上線時 DB 裡會有一萬多筆職缺沒有詳情，用每日流程的小額度要跑好幾週。
這支獨立執行，帶時間預算，跑到接近 Actions 上限就自己停下來，下次接著跑。
"""
import sys
import time
import logging
import argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")

from .client import Client, BlockedError, FetchError   # noqa: E402
from .parse import parse_detail                        # noqa: E402
from .store import Store                               # noqa: E402
from . import notify                                   # noqa: E402
from datetime import date                              # noqa: E402

log = logging.getLogger("backfill")
CHUNK = 500


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=300.0,
                    help="時間預算，留餘裕給 Actions 的 6 小時上限")
    ap.add_argument("--max-jobs", type=int, default=0, help="0 表示不限筆數")
    args = ap.parse_args()

    client = Client()
    store = Store()
    deadline = time.time() + args.max_minutes * 60
    done = failed = 0

    try:
        while True:
            if time.time() >= deadline:
                log.info("已達時間預算，停止")
                break
            if args.max_jobs and done >= args.max_jobs:
                log.info("已達筆數上限，停止")
                break

            targets = store.jobs_needing_detail(CHUNK)
            if not targets:
                log.info("沒有待補的職缺了")
                break

            for row in targets:
                if time.time() >= deadline:
                    break
                enc_id = row.get("enc_id")
                if not enc_id:
                    continue
                try:
                    payload = client.detail(enc_id)
                except FetchError as e:
                    log.warning("失敗 %s: %s", row["job_no"], e)
                    failed += 1
                    continue
                if not payload:
                    # 404：職缺已移除。標記時間戳避免無限重試。
                    store.save_detail(row["job_no"], {"detail_fetched_at": "now()"})
                    continue
                fields = parse_detail(payload)
                fields["detail_fetched_at"] = "now()"
                store.save_detail(row["job_no"], fields)
                done += 1
                if done % 100 == 0:
                    log.info("已補 %s 筆 / 失敗 %s / 剩餘時間 %.0f 分鐘",
                             done, failed, (deadline - time.time()) / 60)

        log.info("回填結束：成功 %s / 失敗 %s / 請求 %s",
                 done, failed, client.request_count)
        return 0

    except BlockedError as e:
        log.error("被 104 擋下，中止：%s", e)
        notify.send_failure(date.today(), "blocked", e,
                            {"jobs_seen": done, "pages": client.request_count})
        return 2


if __name__ == "__main__":
    sys.exit(main())
