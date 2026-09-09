# -*- coding: utf-8 -*-
"""Supabase 存取層。爬蟲使用 service_role key，會繞過 RLS。"""
import time
import logging
import functools
from datetime import date, timedelta

import httpx
from supabase import create_client

from . import config as C

log = logging.getLogger(__name__)

PAGE = 1000       # Supabase 單次 select 的列數上限
# 寫入批次。Supabase 對單一語句有執行時間上限，批次太大會整批被取消
# （2026-09-08 實測 500 筆會超時）。寧可多送幾次請求也不要整批失敗。
BATCH = 200

# 一輪爬蟲要跑四十幾分鐘、送出上千次資料庫請求，其間 Supabase 的 HTTP/2
# 連線偶爾會被回收（2026-09-09 實測：Server disconnected）。
# 這類錯誤是暫時的，重連後就能繼續；讓它中斷整輪太脆弱。
TRANSIENT_ERRORS = (
    httpx.RemoteProtocolError,   # Server disconnected
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
)
DB_BACKOFF = (2, 5, 15)          # 重試次數 = len + 1


def retry_db(fn):
    """連線類錯誤自動重連後重試；資料錯誤（違反約束等）維持立即拋出。"""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        last = None
        for attempt in range(len(DB_BACKOFF) + 1):
            try:
                return fn(self, *args, **kwargs)
            except TRANSIENT_ERRORS as e:
                last = e
                if attempt == len(DB_BACKOFF):
                    break
                wait = DB_BACKOFF[attempt]
                log.warning("資料庫連線中斷（%s，第 %s 次），重連後 %s 秒重試：%s",
                            type(e).__name__, attempt + 1, wait, fn.__name__)
                self.reconnect()
                time.sleep(wait)
        raise last
    return wrapper


class Store:
    def __init__(self, url=None, key=None):
        self._url = url or C.SUPABASE_URL
        self._key = key or C.SUPABASE_SERVICE_KEY
        if not self._url or not self._key:
            raise RuntimeError("缺少 SUPABASE_URL / SUPABASE_SERVICE_KEY")
        self.sb = create_client(self._url, self._key)
        self.reconnects = 0

    def reconnect(self):
        """丟掉舊的連線池，重新建立 client。"""
        self.reconnects += 1
        self.sb = create_client(self._url, self._key)

    # ---------------- runs（監控用，沒有這個就察覺不到靜默死亡）----------
    @retry_db
    def start_run(self):
        r = self.sb.table("runs").insert({"status": "running"}).execute()
        return r.data[0]["id"]

    @retry_db
    def finish_run(self, run_id, status, stats=None, warnings=None, error=None):
        payload = {"status": status, "finished_at": "now()"}
        payload.update(stats or {})
        if warnings:
            payload["warnings"] = warnings
        if error:
            payload["error"] = str(error)[:2000]
        self.sb.table("runs").update(payload).eq("id", run_id).execute()

    # ---------------- jobs ----------------
    @retry_db
    def load_open_jobs(self):
        """載入所有在架職缺的比對用欄位。回傳 {job_no: row}。"""
        cols = ("job_no,dedupe_key,first_seen,job_name,salary_low,salary_high,"
                "apply_cnt,appear_date,canonical_job_no,detail_fetched_at")
        out, start = {}, 0
        while True:
            r = (self.sb.table("jobs").select(cols)
                 .eq("is_open", True).order("job_no")
                 .range(start, start + PAGE - 1).execute())
            for row in r.data:
                out[row["job_no"]] = row
            if len(r.data) < PAGE:
                break
            start += PAGE
        log.info("載入在架職缺 %s 筆", len(out))
        return out

    @retry_db
    def recent_dedupe_keys(self, window_days=None):
        """回傳 {dedupe_key: 最早的 job_no}，用於重刊摺疊。"""
        window = window_days or C.RELIST_WINDOW_DAYS
        since = (date.today() - timedelta(days=window)).isoformat()
        best, start = {}, 0
        while True:
            r = (self.sb.table("jobs")
                 .select("job_no,dedupe_key,first_seen,canonical_job_no")
                 .gte("last_seen", since).order("job_no")
                 .range(start, start + PAGE - 1).execute())
            for row in r.data:
                key = row.get("dedupe_key")
                if not key:
                    continue
                canonical = row.get("canonical_job_no") or row["job_no"]
                seen_at = row.get("first_seen") or ""
                prev = best.get(key)
                if prev is None or seen_at < prev[1]:
                    best[key] = (canonical, seen_at)
            if len(r.data) < PAGE:
                break
            start += PAGE
        return {k: v[0] for k, v in best.items()}

    @retry_db
    def _upsert_chunk(self, chunk):
        self.sb.table("jobs").upsert(chunk).execute()

    def upsert_jobs(self, rows):
        for i in range(0, len(rows), BATCH):
            self._upsert_chunk(rows[i:i + BATCH])

    @retry_db
    def lookup_jobs(self, job_nos):
        """查這批 job_no 的既有狀態（含已下架的），用來辨識回鍋職缺。

        少了這一步，下架後又重新上架的職缺會被當成全新職缺，
        upsert 時 first_seen 會被今天覆蓋掉，歷史就毀了。
        """
        out, ids = {}, list(job_nos)
        for i in range(0, len(ids), BATCH):
            r = (self.sb.table("jobs")
                 .select("job_no,first_seen,is_open,dedupe_key,canonical_job_no")
                 .in_("job_no", ids[i:i + BATCH]).execute())
            for row in r.data:
                out[row["job_no"]] = row
        return out

    @retry_db
    def _close_chunk(self, chunk, payload):
        self.sb.table("jobs").update(payload).in_("job_no", chunk).execute()

    def mark_closed(self, job_nos, today):
        payload = {"is_open": False, "closed_at": today.isoformat()}
        for i in range(0, len(job_nos), BATCH):
            self._close_chunk(job_nos[i:i + BATCH], payload)

    @retry_db
    def jobs_needing_detail(self, limit):
        r = (self.sb.table("jobs").select("job_no,enc_id")
             .is_("detail_fetched_at", "null").eq("is_open", True)
             .order("first_seen", desc=True).limit(limit).execute())
        return r.data

    @retry_db
    def save_detail(self, job_no, fields):
        self.sb.table("jobs").update(fields).eq("job_no", job_no).execute()

    # ---------------- events ----------------
    @retry_db
    def _insert_event_chunk(self, chunk):
        self.sb.table("job_events").insert(chunk).execute()

    def insert_events(self, events):
        for i in range(0, len(events), BATCH):
            self._insert_event_chunk(events[i:i + BATCH])

    # ---------------- queries（搜尋條件存 DB，不寫死）----------------
    @retry_db
    def load_queries(self):
        r = self.sb.table("queries").select("*").eq("enabled", True).execute()
        return r.data

    @retry_db
    def sync_queries(self, rows):
        if rows:
            self.sb.table("queries").upsert(
                rows, on_conflict="jobcat,area").execute()

    @retry_db
    def blocked_cust_nos(self):
        r = self.sb.table("blocked_companies").select("cust_no").execute()
        return {row["cust_no"] for row in r.data}
