# -*- coding: utf-8 -*-
"""104 API client：節流、重試、以及「被擋」的明確偵測。

被擋必須是一個明確的例外而不是空結果，否則爬蟲會靜默地把
「今天抓到 0 筆」誤判成「今天所有職缺都下架了」，寫出一堆假的 closed 事件。
"""
import time
import logging
import requests

from . import config as C

log = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """104 拒絕服務（403/429/回傳非 JSON）。呼叫端必須中止而非續跑。"""


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
        self._last_request_at = 0.0

    # ------------------------------------------------------------------
    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_at = time.monotonic()

    def _get_json(self, url, params=None, referer=None):
        headers = {"Referer": referer} if referer else {}
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            self.request_count += 1
            try:
                r = self.session.get(url, params=params, headers=headers,
                                     timeout=self.timeout)
            except requests.RequestException as e:
                last_exc = e
                log.warning("request error (%s/%s): %s", attempt, self.max_retries, e)
                time.sleep(self.delay * attempt * 2)
                continue

            if r.status_code in (403, 429):
                raise BlockedError(
                    f"HTTP {r.status_code} from {url} — 極可能是 IP 被 104 風控擋下"
                )
            if r.status_code >= 500:
                last_exc = FetchError(f"HTTP {r.status_code}")
                log.warning("server error %s (%s/%s)", r.status_code, attempt, self.max_retries)
                time.sleep(self.delay * attempt * 2)
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
