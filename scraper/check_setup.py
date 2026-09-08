# -*- coding: utf-8 -*-
"""上線前的環境檢查：金鑰、資料表、寫入權限、登入帳號、RLS、Telegram。

RLS 那項特別重要。anon key 一定會出現在前端原始碼裡，
如果 RLS 沒生效，等於把整個資料庫公開在網路上。
這支程式會實際插入一筆測試資料、用 anon 身分嘗試讀取、再清掉，
用真實行為驗證而不是相信設定畫面。
"""
import os
import sys
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.WARNING)

from supabase import create_client          # noqa: E402
from . import config as C                   # noqa: E402

TABLES = ["jobs", "job_events", "marks", "blocked_companies", "queries", "runs"]
TEST_JOB_NO = "__rls_probe__"

OK, FAIL, WARN = "  OK  ", " FAIL ", " WARN "
failures = []


def line(status, label, detail=""):
    print(f"[{status}] {label:<26} {detail}")


def main():
    print("=" * 70)
    print("Supabase 環境檢查")
    print("=" * 70)

    # ---------- 1. 環境變數 ----------
    url = C.SUPABASE_URL
    key = C.SUPABASE_SERVICE_KEY
    anon = os.getenv("SUPABASE_ANON_KEY", "")

    if not url or "xxxx" in url:
        line(FAIL, "SUPABASE_URL", "未設定或仍是佔位符")
        failures.append("url")
        return 1
    line(OK, "SUPABASE_URL", url)

    if not key or key.startswith(("eyJhbGciOi...", "sb_secret_...")):
        line(FAIL, "SUPABASE_SERVICE_KEY", "未設定或仍是佔位符（.env 還沒填）")
        failures.append("key")
        return 1
    if key.startswith("sb_publishable"):
        # 這兩把金鑰在設定頁上下相鄰，很容易複製錯。
        # 用 publishable key 跑爬蟲會被 RLS 擋住，錯誤訊息卻只說「查無資料」。
        line(FAIL, "SUPABASE_SERVICE_KEY",
             "這是 Publishable key（公開金鑰），不是 Secret key。"
             "請到 API Keys 頁面的 Secret keys 區塊複製 sb_secret_ 開頭的那把")
        failures.append("key-wrong-type")
        return 1
    line(OK, "SUPABASE_SERVICE_KEY", f"已設定（{len(key)} 字元，開頭 {key[:11]}…）")

    # ---------- 2. 連線 ----------
    try:
        sb = create_client(url, key)
    except Exception as e:
        line(FAIL, "建立連線", f"{type(e).__name__}: {e}")
        return 1
    line(OK, "建立連線", "")

    # ---------- 3. 六張表 ----------
    print("-" * 70)
    for t in TABLES:
        try:
            r = sb.table(t).select("*", count="exact").limit(0).execute()
            line(OK, f"表 {t}", f"存在，目前 {r.count} 列")
        except Exception as e:
            line(FAIL, f"表 {t}", f"{type(e).__name__}: {str(e)[:90]}")
            failures.append(f"table:{t}")

    # ---------- 4. 寫入權限 ----------
    print("-" * 70)
    run_id = None
    try:
        r = sb.table("runs").insert({"status": "running"}).execute()
        run_id = r.data[0]["id"]
        sb.table("runs").update({"status": "ok", "finished_at": "now()"}
                                ).eq("id", run_id).execute()
        line(OK, "寫入 runs", f"insert + update 成功（id={run_id}）")
    except Exception as e:
        line(FAIL, "寫入 runs", f"{type(e).__name__}: {str(e)[:90]}")
        failures.append("write")

    # ---------- 5. 登入帳號 ----------
    print("-" * 70)
    try:
        users = sb.auth.admin.list_users()
        if not users:
            line(FAIL, "登入帳號", "一個都沒有 —— 手機將無法登入，請到 Authentication 建立")
            failures.append("auth")
        else:
            for u in users:
                confirmed = getattr(u, "email_confirmed_at", None)
                mark = "已驗證" if confirmed else "未驗證！登入會失敗，請重建並勾 Auto Confirm"
                line(OK if confirmed else FAIL, "登入帳號", f"{u.email} — {mark}")
                if not confirmed:
                    failures.append("auth-unconfirmed")
    except Exception as e:
        line(FAIL, "登入帳號", f"{type(e).__name__}: {str(e)[:90]}")
        failures.append("auth")

    # ---------- 6. RLS 實測 ----------
    print("-" * 70)
    if not anon or "xxxx" in anon:
        line(WARN, "RLS 實測", "未設定 SUPABASE_ANON_KEY，跳過（做前端前務必補測）")
    else:
        try:
            sb.table("jobs").upsert({
                "job_no": TEST_JOB_NO, "enc_id": "probe", "job_name": "RLS 測試",
                "first_seen": "2000-01-01", "last_seen": "2000-01-01",
            }).execute()
            seen_by_service = sb.table("jobs").select("job_no").eq(
                "job_no", TEST_JOB_NO).execute()

            anon_sb = create_client(url, anon)
            seen_by_anon = anon_sb.table("jobs").select("job_no").eq(
                "job_no", TEST_JOB_NO).execute()

            if len(seen_by_service.data) == 1 and len(seen_by_anon.data) == 0:
                line(OK, "RLS 實測", "service_role 讀得到、anon 讀不到 —— 正確")
            elif seen_by_anon.data:
                line(FAIL, "RLS 實測",
                     "!!! anon 讀得到資料 —— 資料庫對外公開，不要部署前端 !!!")
                failures.append("rls")
            else:
                line(FAIL, "RLS 實測", "service_role 自己也讀不到，寫入或查詢異常")
                failures.append("rls")
        except Exception as e:
            msg = str(e)
            if "401" in msg or "permission" in msg.lower():
                line(OK, "RLS 實測", "anon 被拒絕存取 —— 正確")
            else:
                line(FAIL, "RLS 實測", f"{type(e).__name__}: {msg[:90]}")
                failures.append("rls")
        finally:
            try:
                sb.table("jobs").delete().eq("job_no", TEST_JOB_NO).execute()
            except Exception:
                pass

    # ---------- 7. Telegram ----------
    print("-" * 70)
    if not C.TELEGRAM_BOT_TOKEN or C.TELEGRAM_BOT_TOKEN.startswith("123456789:AA"):
        line(WARN, "Telegram", "未設定（不影響爬蟲，但收不到每日彙總與失敗告警）")
    else:
        import requests
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/getMe", timeout=15)
            if r.status_code == 200 and r.json().get("ok"):
                bot = r.json()["result"]
                line(OK, "Telegram bot", f"@{bot.get('username')}")
                from . import notify
                if notify._send("✅ 104 職缺追蹤：環境檢查通過，推播管道正常。"):
                    line(OK, "Telegram 推播", "測試訊息已送出，請check手機")
                else:
                    line(FAIL, "Telegram 推播", "送出失敗，chat id 可能不對")
                    failures.append("telegram-send")
            else:
                line(FAIL, "Telegram bot", f"token 無效（HTTP {r.status_code}）")
                failures.append("telegram")
        except Exception as e:
            line(FAIL, "Telegram", f"{type(e).__name__}: {str(e)[:90]}")
            failures.append("telegram")

    # ---------- 收尾 ----------
    print("=" * 70)
    if failures:
        print(f"結論：{len(failures)} 項未通過 -> {failures}")
        return 1
    print("結論：全部通過，可以開始跑小規模驗證。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
