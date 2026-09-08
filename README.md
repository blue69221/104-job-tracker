# 104 新竹硬體職缺追蹤

每天自動掃描 104 上新竹縣市的硬體相關職缺，偵測新增／重刊／下架，
用 Telegram 推播每日彙總，資料存在 Supabase，手機可瀏覽與標記。

## 架構

```
GitHub Actions (每日 09:00)
   └─ scraper/run_daily.py
        ├─ planner   把抓取範圍拆成 61 個不會撞 104 分頁上限的子查詢
        ├─ client    104 API，含節流、重試、被擋偵測
        ├─ parse     正規化 + 重刊摺疊鍵
        ├─ store     Supabase 讀寫（service_role，繞過 RLS）
        └─ notify    Telegram 每日彙總 + 失敗告警
                          │
                    Supabase Postgres
                          │
                    靜態網頁（手機瀏覽 + 標記）
```

## 為什麼是這樣設計的

這些是實測後才確定的限制，改動前請先看懂：

| 事實 | 後果 |
|---|---|
| `pagesize` 只接受 20，`lastPage` 封頂 150 | 單一查詢最多取 3,000 筆，**超出的部分不會報錯，直接消失** |
| 目標範圍有 10,500+ 筆 | 必須拆成 61 個子查詢；任一子查詢超過 2,800 筆會自動再按地區細分 |
| 詳情頁只能用網址短碼（`4mzei`），`jobNo` 會 404 | `enc_id` 必須存下來 |
| 列表沒有薪資字串、經驗、學歷、技能、福利 | 這些只有詳情頁有，且職缺下架後就永遠抓不到 |
| 公司常下架再上架來刷排序，會產生新的 `jobNo` | 用「公司編號＋正規化職稱＋地區＋60 天內」保守摺疊為重刊，不進推播 |
| Supabase 免費 500MB | 用事件式儲存（只記變化），不做每日全量快照 |
| Actions 排程在 repo 60 天無 commit 後會停用 | 每日流程推一次 heartbeat commit |

## 部署

### 1. Supabase

建立免費專案，在 SQL Editor 執行 `sql/schema.sql`。
記下 Project URL、`anon` key（前端用）、`service_role` key（爬蟲用）。

> `anon` key 一定會出現在前端原始碼裡。schema 已為所有表開啟 RLS 且
> 未給 `anon` 任何 policy，未登入無法讀寫任何資料。不要關掉 RLS。

在 Authentication 建立你自己的帳號（Email + Password），這是唯一的登入者。

### 2. Telegram

跟 `@BotFather` 對話建立 bot，取得 token；跟 `@userinfobot` 對話取得你的 chat id。

### 3. GitHub

Settings → Secrets and variables → Actions：

| 類型 | 名稱 |
|---|---|
| Secret | `SUPABASE_URL` |
| Secret | `SUPABASE_SERVICE_KEY` |
| Secret | `TELEGRAM_BOT_TOKEN` |
| Secret | `TELEGRAM_CHAT_ID` |
| Variable | `WEB_URL`（前端網址，之後才有） |

### 4. 上線順序

照順序做，**不要跳過第 2 步**——全量掃描要 27 分鐘，schema 有問題的話會寫進一萬多筆髒資料。

1. **確認 Actions 的機房 IP 沒被 104 擋**：手動觸發 `probe-104`
2. **小規模驗證寫入**（本機跑，只掃 3 個查詢、約 1 分鐘）：

   ```bash
   python -m scraper.run_daily --limit-queries 3 --detail-budget 5 --no-notify
   ```

   然後到 Supabase 檢查 `jobs`、`job_events`、`runs` 三張表的內容對不對。
   這個模式會自動停用下架判定，因為沒掃完全部。
3. **第一次全量掃描**：手動觸發 `daily-104`
4. **補齊詳情頁**：手動觸發 `backfill-details`，跑不完就再觸發一次

之後每天 09:00 自動執行。

## 本機執行

```bash
pip install -r requirements.txt
cp .env.example .env    # 填入你的金鑰
python -m scraper.probe          # 連線測試
python -m scraper.show_plan      # 看查詢計畫，確認沒有查詢會被截斷
python -m scraper.run_daily --no-notify --detail-budget 0
```

## 調整抓取範圍

改 `scraper/config.py` 的 `JOBCAT_GROUPS` 與 `AREA_ROOT`。
代碼可在 `https://static.104.com.tw/category-tool/json/JobCat.json` 查到。
改完先跑 `python -m scraper.show_plan` 確認沒有查詢會被截斷。

目前設定（新竹縣市 `6001006000`）：

- `2008001000` 工程研發類人員（硬體研發、電子、數位／類比 IC、IC 佈局、半導體、PCB、RF…）
- `2007001005` 韌體工程師
- `2009002000` 製程規劃類人員（半導體製程／設備工程師）
- `2009003000` 品保／品管類人員（硬體測試、IC 封測、EMC）

## 手機網頁

**https://blue69221.github.io/104-job-tracker/**

單一 HTML 檔（`docs/index.html`），走 GitHub Pages，無建置流程。
以 Supabase Authentication 的帳號登入，資料透過 `v_job_list` 檢視表查詢。

前端帶的是 **anon（publishable）金鑰**，那是公開金鑰，出現在原始碼是設計如此。
保護來自 RLS：未登入者對任何一張表或 view 都沒有 policy，一列都讀不到。
**改動 `sql/views.sql` 時務必保留 `security_invoker = true`**，否則 view 會以
建立者權限執行，等於在 RLS 上開一個後門。

修改前端後推上 main 即自動部署，約一分鐘生效。

## 維運

| 狀況 | 處理 |
|---|---|
| 收到「爬蟲失敗」的 Telegram | 看 `runs` 表的 `error` 欄位，或 Actions 的 log |
| 錯誤是 HTTP 429 | 暫時性限流，會自動退避重試；連續失敗就隔一段時間重跑 |
| 錯誤是 HTTP 403 | IP 被封鎖，考慮把爬蟲改跑在家用網路 |
| 連續幾天沒收到任何 Telegram | 排程可能被停用，檢查 Actions 頁面與 `state/heartbeat.txt` |
| 想補完歷史職缺的詳情 | 手動觸發 `backfill-details`，每次 1,500 筆 |
| 想改抓取範圍 | 改 `scraper/config.py` 的 `JOBCAT_GROUPS`，先跑 `show_plan` 確認沒有查詢會被截斷 |

診斷指令：

```bash
python -m scraper.check_setup   # 金鑰、資料表、RLS、Telegram 全部驗一次
python -m scraper.show_data     # 看資料庫現況與最近幾次執行
python -m scraper.show_plan     # 看查詢計畫，確認覆蓋率
```

## 已知限制

- 「哪一天沒跑」在事件式儲存下無法與「那天沒有變化」區分。`runs` 表可以還原執行歷史。
- 重刊摺疊是保守的：公司若同時改了職稱與地區，會被當成新職缺。
- 104 若調整職類分類，`show_plan` 會反映出來，但舊資料的 `job_cats` 不會回溯更新。
