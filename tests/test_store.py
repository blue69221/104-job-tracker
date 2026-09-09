# -*- coding: utf-8 -*-
"""資料庫連線的重試行為。

一輪爬蟲要跑四十幾分鐘、送出上千次資料庫請求，Supabase 的 HTTP/2 連線
偶爾會被回收。少了重試，一次斷線就讓整輪失敗。
"""
import unittest
from unittest import mock

import httpx

from scraper.store import retry_db, DB_BACKOFF


class FakeStore:
    """模擬 Store：依序拋出指定的例外，用完就成功。"""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.reconnects = 0

    def reconnect(self):
        self.reconnects += 1

    @retry_db
    def op(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


class TestRetryDb(unittest.TestCase):

    @mock.patch("scraper.store.time.sleep")
    def test_disconnect_retries_after_reconnecting(self, sleep):
        """Server disconnected 是暫時的，重連後應該繼續。"""
        s = FakeStore([httpx.RemoteProtocolError("Server disconnected")])
        self.assertEqual(s.op(), "ok")
        self.assertEqual(s.calls, 2)
        self.assertEqual(s.reconnects, 1)
        self.assertEqual(sleep.call_args_list[0].args[0], DB_BACKOFF[0])

    @mock.patch("scraper.store.time.sleep")
    def test_backoff_grows_between_attempts(self, sleep):
        s = FakeStore([httpx.ConnectError("x"), httpx.ReadTimeout("y")])
        self.assertEqual(s.op(), "ok")
        self.assertEqual(s.calls, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],
                         list(DB_BACKOFF[:2]))

    @mock.patch("scraper.store.time.sleep")
    def test_gives_up_after_all_attempts(self, sleep):
        s = FakeStore([httpx.RemoteProtocolError("x")] * 10)
        with self.assertRaises(httpx.RemoteProtocolError):
            s.op()
        self.assertEqual(s.calls, len(DB_BACKOFF) + 1)

    @mock.patch("scraper.store.time.sleep")
    def test_data_errors_are_not_retried(self, sleep):
        """違反約束之類的資料錯誤重試幾次都一樣，應該立刻拋出。"""
        s = FakeStore([ValueError("null value violates not-null constraint")])
        with self.assertRaises(ValueError):
            s.op()
        self.assertEqual(s.calls, 1)
        self.assertEqual(s.reconnects, 0)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
