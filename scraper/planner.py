# -*- coding: utf-8 -*-
"""查詢規劃：把抓取範圍拆成一組保證不會撞 104 分頁上限的子查詢。

104 的硬限制（階段 0 實測）：pagesize 只能是 20、lastPage 封頂 150，
也就是任何單一查詢最多只能取到 3000 筆，超出的部分不會有任何錯誤訊息。
所以規劃階段必須先查總筆數，超過安全線就往下細分，否則會靜默漏抓。
"""
import logging
from dataclasses import dataclass, field

from . import config as C
from .codes import index_tree, leaves_under, path_of

log = logging.getLogger(__name__)


@dataclass
class Query:
    jobcat: str
    area: str
    label: str
    total: int
    pages: int = field(init=False)

    def __post_init__(self):
        capped = min(self.total, C.HARD_CAP)
        self.pages = min((capped + C.PAGE_SIZE - 1) // C.PAGE_SIZE, C.MAX_PAGE)

    @property
    def truncated(self):
        return self.total > C.HARD_CAP


def build_plan(client, jobcat_groups=None, area_root=None):
    """回傳 (queries, warnings)。warnings 非空代表仍有查詢會被截斷。"""
    jobcat_groups = jobcat_groups or C.JOBCAT_GROUPS
    area_root = area_root or C.AREA_ROOT

    raw_jobcat, raw_area = client.fetch_codes()
    jc_index = index_tree(raw_jobcat)
    ar_index = index_tree(raw_area)

    leaf_cats = []
    for group in jobcat_groups:
        for leaf in leaves_under(jc_index, group):
            if leaf not in leaf_cats:
                leaf_cats.append(leaf)
    log.info("展開 %s 組職類 -> %s 個葉節點", len(jobcat_groups), len(leaf_cats))

    sub_areas = ar_index[area_root]["children"]
    queries, warnings = [], []

    for cat in leaf_cats:
        total = client.search_total(cat, area_root)
        label = path_of(jc_index, cat)
        if total == 0:
            continue
        if total <= C.SPLIT_THRESHOLD:
            queries.append(Query(cat, area_root, label, total))
            continue

        # 超過安全線 -> 按地區細分
        log.info("「%s」在 %s 有 %s 筆，超過安全線，改按地區細分",
                 label, ar_index[area_root]["des"], total)
        for area in sub_areas:
            sub_total = client.search_total(cat, area)
            if sub_total == 0:
                continue
            q = Query(cat, area, f"{label} @ {ar_index[area]['des']}", sub_total)
            if q.truncated:
                msg = (f"「{q.label}」有 {sub_total} 筆，已細到最小地區仍超過 "
                       f"{C.HARD_CAP} 上限，會漏抓 {sub_total - C.HARD_CAP} 筆")
                log.error(msg)
                warnings.append(msg)
            queries.append(q)

    return queries, warnings


def plan_summary(queries):
    total_pages = sum(q.pages for q in queries)
    return {
        "queries": len(queries),
        "pages": total_pages,
        "records": sum(min(q.total, C.HARD_CAP) for q in queries),
        "est_minutes": round(total_pages * C.DELAY / 60, 1),
    }
