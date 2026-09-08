# -*- coding: utf-8 -*-
"""集中所有常數與環境變數。改行為請動這裡，不要散在各處。"""
import os
from pathlib import Path

# 本機執行時從專案根目錄的 .env 載入；GitHub Actions 上沒有這個檔案，
# 環境變數由 repo Secrets 直接注入，load_dotenv 找不到檔案也不會出錯。
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:      # 未安裝 python-dotenv 時退回純環境變數
    pass

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
# 實測（2026-09-08）：列表 1,370 頁 + 400 筆詳情共 1,770 次請求可以通過；
# 但以 1.2 秒間隔連續抓 2,500 筆詳情（約 48 分鐘）就會收到 HTTP 429。
# 詳情頁的容忍度明顯比列表低，所以獨立設定較慢的間隔。
DELAY = float(os.getenv("SCRAPE_DELAY", "1.2"))
DETAIL_DELAY = float(os.getenv("SCRAPE_DETAIL_DELAY", "2.0"))
TIMEOUT = int(os.getenv("SCRAPE_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("SCRAPE_MAX_RETRIES", "3"))

# 429 是暫時性限流，不是封鎖。等待後重試通常能繼續，
# 直接中止會讓回填永遠補不完。403 才視為真正被擋。
RATE_LIMIT_BACKOFF = [int(x) for x in
                      os.getenv("RATE_LIMIT_BACKOFF", "60,180,420").split(",")]
# 單次回填的筆數上限。連續請求越久越容易觸線，分多次跑比一次跑完安全。
BACKFILL_MAX_JOBS = int(os.getenv("BACKFILL_MAX_JOBS", "1500"))

# --- 業務規則 ---
RELIST_WINDOW_DAYS = 60   # 重刊摺疊的回溯窗（第 8 題）

# --- 外部服務 ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
