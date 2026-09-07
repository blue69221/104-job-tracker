# -*- coding: utf-8 -*-
"""階段 1 gate：確認這台機器（尤其 GitHub Actions 的機房 IP）能不能存取 104。

跑得過才值得往下蓋。跑不過就得把爬蟲移到家用 IP。
"""
import sys
import time
import logging
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from .client import Client, BlockedError, FetchError  # noqa: E402
from . import config as C  # noqa: E402


def main():
    print("=" * 60)
    print("104 連線探測")
    print("=" * 60)

    try:
        ip = requests.get("https://api.ipify.org", timeout=15).text.strip()
        print(f"出口 IP: {ip}")
    except Exception as e:
        print(f"出口 IP: 查不到 ({e})")

    client = Client()
    failures = []

    # 1. 代碼表
    try:
        jobcat, area = client.fetch_codes()
        print(f"[1/4] 代碼表        OK  職類 {len(jobcat)} 大類 / 地區 {len(area)} 區")
    except Exception as e:
        print(f"[1/4] 代碼表        FAIL  {type(e).__name__}: {e}")
        failures.append("codes")

    # 2. 搜尋 API
    first_job = None
    try:
        j = client.search(C.JOBCAT_GROUPS[0], C.AREA_ROOT, page=1)
        pg = j["metadata"]["pagination"]
        first_job = j["data"][0] if j["data"] else None
        print(f"[2/4] 搜尋 API      OK  total={pg['total']} lastPage={pg['lastPage']} "
              f"本頁 {len(j['data'])} 筆")
    except Exception as e:
        print(f"[2/4] 搜尋 API      FAIL  {type(e).__name__}: {e}")
        failures.append("search")

    # 3. 詳情 API
    if first_job:
        try:
            enc = first_job["link"]["job"].rstrip("/").split("/")[-1]
            d = client.detail(enc)
            ok = bool(d and d.get("data"))
            print(f"[3/4] 詳情 API      {'OK' if ok else 'FAIL'}  enc_id={enc} "
                  f"sections={len(d['data']) if ok else 0}")
            if not ok:
                failures.append("detail")
        except Exception as e:
            print(f"[3/4] 詳情 API      FAIL  {type(e).__name__}: {e}")
            failures.append("detail")
    else:
        print("[3/4] 詳情 API      SKIP（搜尋沒回傳職缺）")

    # 4. 連續請求耐受度：真正跑起來是上千次請求，20 次通過不代表安全，
    #    但 20 次就被擋代表一定不安全。
    print(f"[4/4] 連續 20 次請求（間隔 {C.DELAY}s）...")
    t0 = time.time()
    try:
        for page in range(1, 21):
            client.search(C.JOBCAT_GROUPS[0], C.AREA_ROOT, page=page)
        print(f"       OK  20 頁耗時 {time.time() - t0:.0f}s，未被擋")
    except BlockedError as e:
        print(f"       BLOCKED  {e}")
        failures.append("sustained")
    except FetchError as e:
        print(f"       FAIL  {e}")
        failures.append("sustained")

    print("=" * 60)
    if failures:
        print(f"結論：失敗項目 {failures} —— 這個執行環境不可用，爬蟲需改跑在家用 IP")
        return 1
    print(f"結論：全部通過，共送出 {client.request_count} 次請求。此環境可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
