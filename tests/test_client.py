# -*- coding: utf-8 -*-
"""限流與封鎖的處理。這段錯了，回填要不是永遠補不完，就是把 IP 打到真的被封。"""
import unittest
from unittest import mock

from scraper.client import Client, BlockedError, FetchError
from scraper import config as C


def resp(status, payload=None, headers=None):
    r = mock.Mock()
    r.status_code = status
    r.headers = headers if headers is not None else {"content-type": "application/json"}
    r.json.return_value = payload if payload is not None else {}
    r.url = "https://test/x"
    return r


class TestRateLimit(unittest.TestCase):

    def setUp(self):
        self.client = Client(delay=0)
        self.client.session = mock.Mock()

    @mock.patch("scraper.client.time.sleep")
    def test_429_backs_off_then_succeeds(self, sleep):
        """429 是暫時性的，退避後應該繼續，不能當成致命錯誤。"""
        self.client.session.get.side_effect = [
            resp(429), resp(429), resp(200, {"ok": True}),
        ]
        self.assertEqual(self.client._get_json("https://test/x"), {"ok": True})
        self.assertEqual(self.client.rate_limit_hits, 2)
        self.assertEqual(sleep.call_args_list[0].args[0], C.RATE_LIMIT_BACKOFF[0])
        self.assertEqual(sleep.call_args_list[1].args[0], C.RATE_LIMIT_BACKOFF[1])

    @mock.patch("scraper.client.time.sleep")
    def test_429_exhausted_raises_blocked_not_fetch_error(self, sleep):
        """退避用盡必須是 BlockedError。報成 FetchError 會讓呼叫端誤判成
        單筆失敗而繼續猛打，把暫時限流變成真正的封鎖。"""
        self.client.session.get.side_effect = [resp(429)] * 10
        with self.assertRaises(BlockedError) as cm:
            self.client._get_json("https://test/x")
        self.assertIn("429", str(cm.exception))
        self.assertEqual(self.client.rate_limit_hits, len(C.RATE_LIMIT_BACKOFF))

    @mock.patch("scraper.client.time.sleep")
    def test_429_respects_retry_after_header(self, sleep):
        self.client.session.get.side_effect = [
            resp(429, headers={"Retry-After": "42"}),
            resp(200, {"ok": True}),
        ]
        self.client._get_json("https://test/x")
        self.assertEqual(sleep.call_args_list[0].args[0], 42)

    @mock.patch("scraper.client.time.sleep")
    def test_throttling_does_not_consume_error_retries(self, sleep):
        """兩種重試分開計數：先被限流數次，之後仍應保有完整的錯誤重試額度。"""
        self.client.session.get.side_effect = [
            resp(429), resp(500), resp(500), resp(200, {"ok": True}),
        ]
        self.assertEqual(self.client._get_json("https://test/x"), {"ok": True})

    @mock.patch("scraper.client.time.sleep")
    def test_403_raises_immediately_without_retry(self, sleep):
        """403 是真的被封鎖，重試只會更糟。"""
        self.client.session.get.side_effect = [resp(403), resp(200, {"ok": True})]
        with self.assertRaises(BlockedError) as cm:
            self.client._get_json("https://test/x")
        self.assertIn("403", str(cm.exception))
        self.assertEqual(self.client.session.get.call_count, 1)


class TestOtherResponses(unittest.TestCase):

    def setUp(self):
        self.client = Client(delay=0)
        self.client.session = mock.Mock()

    def test_404_returns_none(self):
        """職缺已被移除，不是錯誤。"""
        self.client.session.get.side_effect = [resp(404)]
        self.assertIsNone(self.client._get_json("https://test/x"))

    def test_html_response_treated_as_blocked(self):
        """被導向驗證頁時回的是 HTML。當成空結果會讓爬蟲以為職缺全下架了。"""
        self.client.session.get.side_effect = [
            resp(200, headers={"content-type": "text/html; charset=utf-8"})]
        with self.assertRaises(BlockedError):
            self.client._get_json("https://test/x")

    @mock.patch("scraper.client.time.sleep")
    def test_persistent_5xx_raises_fetch_error(self, sleep):
        self.client.session.get.side_effect = [resp(503)] * 10
        with self.assertRaises(FetchError):
            self.client._get_json("https://test/x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
