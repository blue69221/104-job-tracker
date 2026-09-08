# -*- coding: utf-8 -*-
"""104 API client：節流、重試、以及「被擋」的明確偵測。

被擋必須是一個明確的例外而不是空結果，否則爬蟲會靜默地把
「今天抓到 0 筆」誤判成「今天所有職缺都下架了」，寫出一堆假的 closed 事件。

429 與 403 要分開處理（2026-09-08 實測）：
  429 是暫時性限流，退避後重試通常就能繼續。當成致命錯誤直接中止，
      一萬多筆的詳情回填會永遠補不完。
  403 才是 IP 真的被封鎖，必須立刻停手，繼續打只會讓情況更糟。
"""
import time
import logging
import requests

from . import config as C

log = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """104 拒絕服務。呼叫端必須中止而非續跑。"""


class FetchError(RuntimeError):
    """重試後仍失敗的單次請求錯誤。"""


class Client:
    def __init__(self, delay=None, timeout=None, max_retries=None):
        self.delay = C.DELAY if delay is None else delay
        self.timeout = C.TIMEOUT if timeout is None else timeout
        self.max_retries = C.MAX_RETRIES if max_retries is None else max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": C.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9",
        })
        self.request_count = 0
        self.rate_limit_hits = 0
        self._last_request_at = 0.0

    # ------------------------------------------------------------------
    @staticmethod
    def _retry_after(response):
        """優先照 104 自己給的 Retry-After，沒給才用預設退避表。"""
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(1, min(int(raw), 900))     # 上限 15 分鐘，避免卡死
        except (TypeError, ValueError):
            return None

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_at = time.monotonic()

    def _get_json(self, url, params=None, referer=None):
        """兩種重試分開計數：暫時性限流不應該消耗一般錯誤的重試額度，
        否則連續幾次 429 會被誤報成 FetchError，看不出真正的原因。"""
        headers = {"Referer": referer} if referer else {}
        last_exc = None
        errors = 0        # 連線錯誤與 5xx
        throttles = 0     # 429

        while errors < self.max_retries:
            self._throttle()
            self.request_count += 1
            try:
                r = self.session.get(url, params=params, headers=headers,
                                     timeout=self.timeout)
            except requests.RequestException as e:
                last_exc = e
                errors += 1
                log.warning("request error (%s/%s): %s", errors, self.max_retries, e)
                time.sleep(self.delay * errors * 2)
                continue

            if r.status_code == 429:
                if throttles >= len(C.RATE_LIMIT_BACKOFF):
                    raise BlockedError(
                        f"HTTP 429 from {url} — 退避 {throttles} 次後仍被限流，本輪中止"
                    )
                wait = self._retry_after(r) or C.RATE_LIMIT_BACKOFF[throttles]
                throttles += 1
                self.rate_limit_hits += 1
                log.warning("HTTP 429（第 %s 次退避），等待 %s 秒後重試：%s",
                            throttles, wait, url)
                time.sleep(wait)
                continue

            if r.status_code == 403:
                raise BlockedError(
                    f"HTTP 403 from {url} — IP 極可能已被 104 風控封鎖"
                )

            if r.status_code >= 500:
                last_exc = FetchError(f"HTTP {r.status_code}")
                errors += 1
                log.warning("server error %s (%s/%s)", r.status_code,
                            errors, self.max_retries)
                time.sleep(self.delay * errors * 2)
                continue

            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise FetchError(f"HTTP {r.status_code} from {r.url}")

            ctype = r.headers.get("content-type", "")
            if "json" not in ctype:
                # 回 HTML 通常代表被導去驗證頁 —— 視同被擋，不可當成空結果
                raise BlockedError(
                    f"預期 JSON 但收到 {ctype!r} from {url} — 可能被導向驗證頁"
                )
            try:
                return r.json()
            except ValueError as e:
                raise BlockedError(f"JSON 解析失敗 from {url}: {e}")

        raise FetchError(f"{url} 重試 {self.max_retries} 次後仍失敗: {last_exc}")

    # ------------------------------------------------------------------
    def search(self, jobcat, area, page=1, order=15):
        """回傳一頁搜尋結果的原始 JSON（含 data 與 metadata）。"""
        params = {
            "ro": 0, "jobcat": jobcat, "area": area,
            "order": order, "asc": 0, "page": page, "mode": "s",
            "jobsource": "2018indexpoc", "pagesize": C.PAGE_SIZE,
        }
        j = self._get_json(C.SEARCH_URL, params=params, referer=C.SEARCH_REFERER)
        if j is None:
            raise FetchError(f"search 回傳 404: jobcat={jobcat} area={area} page={page}")
        return j

    def search_total(self, jobcat, area):
        """只取總筆數，用於規劃查詢拆分。"""
        j = self.search(jobcat, area, page=1)
        return j["metadata"]["pagination"]["total"]

    def detail(self, enc_id):
        """職缺詳情。注意必須用網址裡的 encoded id，用 jobNo 會 404。"""
        url = C.DETAIL_URL.format(enc_id=enc_id)
        return self._get_json(url, referer=C.DETAIL_REFERER.format(enc_id=enc_id))

    def fetch_codes(self):
        """抓 104 官方的職類與地區代碼表（每次執行重抓，類別異動不會漏）。"""
        jobcat = self._get_json(C.JOBCAT_URL)
        area = self._get_json(C.AREA_URL)
        return jobcat, area
