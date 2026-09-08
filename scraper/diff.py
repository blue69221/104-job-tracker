# -*- coding: utf-8 -*-
"""事件判定：把「今天看到什麼」與「資料庫裡有什麼」比對成事件。

這是整個系統最容易寫錯、而且錯了無法回頭的一段：
  - 把回鍋職缺當成新職缺 -> first_seen 被覆蓋，歷史永久損毀
  - 掃描不完整還判定下架 -> 一次網路抖動產生上千筆假的 closed 事件
所以獨立成純函式，不碰網路也不碰資料庫，可以直接測試。

另外兩件跟 Supabase 寫入方式有關、不是邏輯但一樣會炸的事：

1. upsert 是 INSERT ... ON CONFLICT。只要有一筆在資料庫不存在就會走 INSERT，
   缺 NOT NULL 欄位會讓整批失敗。所以每一列都必須帶 first_seen ——
   要更新的列帶的是「從資料庫查回來的原值」，寫回去等於沒變，
   但不再需要仰賴「分類一定正確」這個假設。

2. 同一批 upsert 的欄位集合必須一致，否則只在部分列出現的欄位
   （例如只有重刊才有的 canonical_job_no）可能整批被忽略，
   摺疊結果就靜默地寫不進去。所以每一列都明確給定，沒有就填 None。
"""
from dataclasses import dataclass, field

from .parse import changed_fields


@dataclass
class Classification:
    new_rows: list = field(default_factory=list)      # 首次出現，first_seen = 今天
    update_rows: list = field(default_factory=list)   # 已存在，first_seen = 原值
    events: list = field(default_factory=list)
    discovered: set = field(default_factory=set)      # 真正的新職缺，進推播
    relisted: set = field(default_factory=set)        # 重刊，不進推播
    reopened: set = field(default_factory=set)
    closed: list = field(default_factory=list)


def classify(seen, existing, known, dedupe_map, today, complete):
    """
    seen        {job_no: 今天解析出的欄位}
    existing    {job_no: 資料庫裡在架職缺的比對欄位}
    known       {job_no: 資料庫裡存在但不在架的職缺}（回鍋判定用）
    dedupe_map  {dedupe_key: canonical_job_no}（重刊摺疊用）
    complete    本輪是否所有查詢都成功；False 時絕不判定下架
    """
    r = Classification()
    iso = today.isoformat()
    candidates = set(seen) - set(existing)

    for job_no in sorted(candidates):
        row = dict(seen[job_no])
        prior = known.get(job_no)

        if prior:
            # 回鍋：資料庫已有這筆，寫回它原本的 first_seen 而不是今天
            row["first_seen"] = prior.get("first_seen") or iso
            row["canonical_job_no"] = prior.get("canonical_job_no")
            r.reopened.add(job_no)
            r.update_rows.append(row)
            r.events.append({"job_no": job_no, "event_type": "reopened",
                             "event_date": iso})
            continue

        row["first_seen"] = iso
        canonical = dedupe_map.get(row.get("dedupe_key"))
        if canonical and canonical != job_no:
            row["canonical_job_no"] = canonical
            r.relisted.add(job_no)
            r.events.append({"job_no": job_no, "event_type": "relisted",
                             "event_date": iso,
                             "payload": {"canonical_job_no": canonical}})
        else:
            row["canonical_job_no"] = None
            r.discovered.add(job_no)
            r.events.append({"job_no": job_no, "event_type": "discovered",
                             "event_date": iso})
        r.new_rows.append(row)

    for job_no, parsed in seen.items():
        if job_no in candidates:
            continue
        prior = existing[job_no]
        row = dict(parsed)
        row["first_seen"] = prior.get("first_seen") or iso
        row["canonical_job_no"] = prior.get("canonical_job_no")
        r.update_rows.append(row)

        diff = changed_fields(prior, parsed)
        if diff:
            r.events.append({"job_no": job_no, "event_type": "changed",
                             "event_date": iso, "payload": diff})

    if complete:
        r.closed = sorted(j for j in existing if j not in seen)
        r.events.extend({"job_no": j, "event_type": "closed", "event_date": iso}
                        for j in r.closed)

    return r


def assert_uniform_columns(rows, label=""):
    """同一批 upsert 的欄位集合必須一致，不一致就會有欄位靜默寫不進去。"""
    if not rows:
        return
    first = set(rows[0])
    for i, row in enumerate(rows[1:], 1):
        if set(row) != first:
            missing = first - set(row)
            extra = set(row) - first
            raise ValueError(
                f"{label} 第 {i} 列的欄位與第 0 列不一致："
                f"缺少 {sorted(missing)}，多出 {sorted(extra)}")
