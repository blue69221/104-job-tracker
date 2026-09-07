# -*- coding: utf-8 -*-
"""集中所有常數與環境變數。改行為請動這裡，不要散在各處。"""
import os

# --- 104 API ---
SEARCH_URL = "https://www.104.com.tw/jobs/search/api/jobs"
DETAIL_URL = "https://www.104.com.tw/job/ajax/content/{enc_id}"
JOBCAT_URL = "https://static.104.com.tw/category-tool/json/JobCat.json"
AREA_URL = "https://static.104.com.tw/category-tool/json/Area.json"

SEARCH_REFERER = "https://www.104.com.tw/jobs/search/"
DETAIL_REFERER = "https://www.104.com.tw/job/{enc_id}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# --- 104 的硬限制（階段 0 實測，2026-09-07）---
PAGE_SIZE = 20        # 只接受 20，其他值回 HTTP 400
MAX_PAGE = 150        # lastPage 封頂
HARD_CAP = PAGE_SIZE * MAX_PAGE          # 3000：單一查詢能取到的上限
SPLIT_THRESHOLD = int(os.getenv("SPLIT_THRESHOLD", "2800"))  # 安全線，超過就細分

# --- 抓取範圍（新竹縣市 × 硬體相關四組）---
AREA_ROOT = "6001006000"   # 新竹縣市（已含新竹市與新竹縣全部鄉鎮）
JOBCAT_GROUPS = [
    "2008001000",  # 研發相關類 > 工程研發類人員（硬體核心）
    "2007001005",  # 資訊軟體系統類 > 韌體工程師
    "2009002000",  # 生產製造/品管/環衛類 > 製程規劃類人員
    "2009003000",  # 生產製造/品管/環衛類 > 品保/品管類人員
]

# --- 禮貌性節流 ---
DELAY = float(os.getenv("SCRAPE_DELAY", "1.2"))
TIMEOUT = int(os.getenv("SCRAPE_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("SCRAPE_MAX_RETRIES", "3"))

# --- 業務規則 ---
RELIST_WINDOW_DAYS = 60   # 重刊摺疊的回溯窗（第 8 題）

# --- 外部服務 ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
