# -*- coding: utf-8 -*-
"""事件判定：把「今天看到什麼」與「資料庫裡有什麼」比對成事件。

這是整個系統最容易寫錯、而且錯了無法回頭的一段：
  - 把回鍋職缺當成新職缺 -> first_seen 被覆蓋，歷史永久損毀
  - 掃描不完整還判定下架 -> 一次網路抖動產生上千筆假的 closed 事件
所以獨立成純函式，不碰網路也不碰資料庫，可以直接測試。
"""
from dataclasses import dataclass, field

from .parse import changed_fields


@dataclass
class Classification:
    new_rows: list = field(default_factory=list)      # 要 INSERT（含 first_seen）
    update_rows: list = field(default_factory=list)   # 要 UPDATE（不含 first_seen）
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
        if job_no in known:
            # 回鍋：資料庫已有這筆，刻意不寫 first_seen 以保住原始日期
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
            r.discovered.add(job_no)
            r.events.append({"job_no": job_no, "event_type": "discovered",
                             "event_date": iso})
        r.new_rows.append(row)

    for job_no, parsed in seen.items():
        if job_no in candidates:
            continue
        r.update_rows.append(dict(parsed))
        diff = changed_fields(existing[job_no], parsed)
        if diff:
            r.events.append({"job_no": job_no, "event_type": "changed",
                             "event_date": iso, "payload": diff})

    if complete:
        r.closed = sorted(j for j in existing if j not in seen)
        r.events.extend({"job_no": j, "event_type": "closed", "event_date": iso}
                        for j in r.closed)

    return r
