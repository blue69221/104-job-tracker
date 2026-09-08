# -*- coding: utf-8 -*-
"""把 104 的原始 JSON 正規化成 jobs 表的欄位。"""
import re
import unicodedata
from datetime import date, datetime

# 104 職稱大量夾帶行銷標語，例如
#   「半導體設備-電機維修工程師【年薪14~16個月/ 無經驗可】」
# 這些字串每次重刊都可能改寫，不清掉就無法辨識同一份工作。
_BRACKETED = re.compile(r"[\[\(【（〔<《〈][^\]\)】）〕>》〉]*[\]\)】）〕>》〉]")
_NON_WORD = re.compile(r"[^\w一-鿿]+")


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "")
    t = _BRACKETED.sub(" ", t)
    t = _NON_WORD.sub("", t)
    return t.lower().strip()


def make_dedupe_key(cust_no, job_name, addr_no) -> str:
    """第 8 題的保守摺疊鍵：同公司 + 同正規化職稱 + 同地區。"""
    return f"{cust_no or ''}|{normalize_title(job_name)}|{addr_no or ''}"


def _parse_yyyymmdd(v):
    """回傳 ISO 日期字串而非 date 物件。

    寫入 Supabase 是走 JSON，date 物件不能序列化；整條管線統一用字串，
    跟 first_seen / last_seen 的型別一致。
    """
    if not v:
        return None
    s = str(v)
    try:
        return datetime.strptime(s, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse_list_job(raw: dict, today: date) -> dict:
    """搜尋列表的一筆職缺 -> jobs 欄位。"""
    link = raw.get("link") or {}
    enc_id = (link.get("job") or "").rstrip("/").split("/")[-1]
    job_no = str(raw.get("jobNo") or "")
    job_name = raw.get("jobName") or ""
    addr_no = raw.get("jobAddrNo")

    return {
        "job_no": job_no,
        "enc_id": enc_id,
        "job_name": job_name,
        "cust_no": raw.get("custNo"),
        "cust_name": raw.get("custName"),
        "co_industry_desc": raw.get("coIndustryDesc"),
        "job_addr_no": str(addr_no) if addr_no is not None else None,
        "job_addr_desc": raw.get("jobAddrNoDesc"),
        "job_address": raw.get("jobAddress"),
        "appear_date": _parse_yyyymmdd(raw.get("appearDate")),
        "apply_cnt": raw.get("applyCnt"),
        "salary_low": raw.get("salaryLow"),
        "salary_high": raw.get("salaryHigh"),
        "employee_count": raw.get("employeeCount"),
        "job_cats": [str(c) for c in (raw.get("jobCat") or [])],
        "description": raw.get("description"),
        "list_snapshot": raw,
        "dedupe_key": make_dedupe_key(raw.get("custNo"), job_name, addr_no),
        "last_seen": today.isoformat(),
        "is_open": True,
    }


def parse_detail(payload: dict) -> dict:
    """詳情頁 -> 供前端篩選的抽出欄位 + 完整快照。

    列表頁的 salaryLow/High 在「面議」時是 0，只有詳情頁才有可讀的薪資字串，
    以及工作經驗、學歷、技能、福利、聯絡方式。
    """
    data = (payload or {}).get("data") or {}
    detail_block = data.get("jobDetail") or {}
    condition = data.get("condition") or {}

    return {
        "detail": data,
        "salary_text": detail_block.get("salary"),
        "salary_min": detail_block.get("salaryMin"),
        "salary_max": detail_block.get("salaryMax"),
        "work_exp": condition.get("workExp"),
        "edu": condition.get("edu"),
    }


def changed_fields(old: dict, new: dict, watch=("job_name", "salary_low", "salary_high",
                                                "apply_cnt", "appear_date")) -> dict:
    """回傳有變動的欄位 {field: [舊值, 新值]}，用來產生 changed 事件。"""
    diff = {}
    for f in watch:
        o, n = old.get(f), new.get(f)
        if isinstance(o, date):
            o = o.isoformat()
        if isinstance(n, date):
            n = n.isoformat()
        if o != n:
            diff[f] = [o, n]
    return diff
