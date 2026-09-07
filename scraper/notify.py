# -*- coding: utf-8 -*-
"""Telegram 推播：每日彙總 + 失敗告警。

失敗告警比彙總更重要 —— 沒有它，爬蟲靜默死亡兩週後你才會發現，
而那段期間的 first_seen 與事件是永遠補不回來的。
"""
import html
import logging
import requests

from . import config as C

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3900          # Telegram 上限 4096，留餘裕


def _send(text):
    if not C.TELEGRAM_BOT_TOKEN or not C.TELEGRAM_CHAT_ID:
        log.warning("未設定 Telegram，訊息未送出:\n%s", text)
        return False
    try:
        r = requests.post(
            API.format(token=C.TELEGRAM_BOT_TOKEN),
            json={
                "chat_id": C.TELEGRAM_CHAT_ID,
                "text": text[:MAX_LEN],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20)
        if r.status_code != 200:
            log.error("Telegram 回應 %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except requests.RequestException as e:
        log.error("Telegram 送出失敗: %s", e)
        return False


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _link(url, text):
    return "<a href=" + '"' + url + '"' + ">" + text + "</a>"


def send_daily(today, stats, new_jobs, web_url=None, warnings=None):
    """new_jobs: [{job_name, cust_name, job_addr_desc, apply_cnt, enc_id}, ...]"""
    head = today.strftime("%m/%d")
    lines = ["<b>104 新竹硬體職缺 · " + head + "</b>"]
    lines.append(
        "新增 <b>{}</b> · 重刊 {} · 下架 {} · 在架 {:,}".format(
            stats.get("new_jobs", 0), stats.get("relisted", 0),
            stats.get("closed", 0), stats.get("jobs_seen", 0)))

    if new_jobs:
        lines.append("")
        for j in new_jobs[:10]:
            url = "https://www.104.com.tw/job/" + str(j.get("enc_id") or "")
            meta = [_e(j.get("job_addr_desc"))]
            if j.get("apply_cnt"):
                meta.append(str(j["apply_cnt"]) + " 人應徵")
            meta_str = " · ".join(x for x in meta if x)
            lines.append("• " + _link(url, _e(j.get("job_name"))))
            tail = " · " + meta_str if meta_str else ""
            lines.append("  " + _e(j.get("cust_name")) + tail)
        if len(new_jobs) > 10:
            lines.append("")
            lines.append("…另外還有 {} 筆".format(len(new_jobs) - 10))
    else:
        lines.append("")
        lines.append("今天沒有新職缺。")

    if warnings:
        lines.append("")
        lines.append("⚠️ {} 個規劃警告，可能有漏抓".format(len(warnings)))

    if web_url:
        lines.append("")
        lines.append(_link(web_url, "開啟完整清單 →"))

    return _send("\n".join(lines))


def send_failure(today, status, error, stats=None):
    head = today.strftime("%m/%d")
    lines = ["⚠️ <b>104 爬蟲失敗 · " + head + "</b>",
             "狀態：<code>" + _e(status) + "</code>"]
    if stats:
        lines.append("已抓 {} 頁 / {} 筆".format(
            stats.get("pages", 0), stats.get("jobs_seen", 0)))
    lines.append("")
    lines.append("<pre>" + _e(str(error)[:600]) + "</pre>")
    if status == "blocked":
        lines.append("")
        lines.append("可能是 IP 被 104 風控擋下，需考慮改由家用網路執行。")
    return _send("\n".join(lines))
