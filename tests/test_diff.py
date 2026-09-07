# -*- coding: utf-8 -*-
"""事件判定的回歸測試。這些情境錯了會造成不可逆的資料損壞。"""
import unittest
from datetime import date

from scraper.diff import classify
from scraper.parse import normalize_title, make_dedupe_key

TODAY = date(2026, 9, 7)


def job(job_no, name="硬體研發工程師", cust="123", addr="6001006001", **kw):
    row = {
        "job_no": job_no, "enc_id": "enc" + job_no, "job_name": name,
        "cust_no": cust, "job_addr_no": addr,
        "dedupe_key": make_dedupe_key(cust, name, addr),
        "salary_low": 0, "salary_high": 0, "apply_cnt": 0,
        "appear_date": None, "last_seen": TODAY.isoformat(), "is_open": True,
    }
    row.update(kw)
    return row


def existing_row(job_no, first_seen="2026-01-01", **kw):
    row = {"job_no": job_no, "first_seen": first_seen,
           "job_name": "硬體研發工程師", "salary_low": 0, "salary_high": 0,
           "apply_cnt": 0, "appear_date": None,
           "dedupe_key": None, "canonical_job_no": None}
    row.update(kw)
    return row


class TestClassify(unittest.TestCase):

    def test_brand_new_job_is_discovered(self):
        seen = {"A": job("A")}
        r = classify(seen, {}, {}, {}, TODAY, complete=True)
        self.assertEqual(r.discovered, {"A"})
        self.assertEqual(r.new_rows[0]["first_seen"], "2026-09-07")
        self.assertEqual([e["event_type"] for e in r.events], ["discovered"])

    def test_relist_is_folded_and_excluded_from_discovered(self):
        """同公司 + 同正規化職稱 + 同地區，60 天內看過 -> 重刊，不進推播。"""
        old = job("OLD")
        new = job("NEW", name="硬體研發工程師【急徵/月薪5萬起】")
        self.assertEqual(old["dedupe_key"], new["dedupe_key"])

        r = classify({"NEW": new}, {}, {}, {new["dedupe_key"]: "OLD"},
                     TODAY, complete=True)
        self.assertEqual(r.relisted, {"NEW"})
        self.assertEqual(r.discovered, set())
        self.assertEqual(r.new_rows[0]["canonical_job_no"], "OLD")

    def test_reopened_job_keeps_original_first_seen(self):
        """回鍋職缺絕不能帶 first_seen，否則會覆蓋掉原始日期。"""
        seen = {"A": job("A")}
        known = {"A": {"job_no": "A", "first_seen": "2025-03-01", "is_open": False}}
        r = classify(seen, {}, known, {}, TODAY, complete=True)

        self.assertEqual(r.reopened, {"A"})
        self.assertEqual(r.new_rows, [])
        self.assertNotIn("first_seen", r.update_rows[0])
        self.assertEqual([e["event_type"] for e in r.events], ["reopened"])

    def test_incomplete_scan_never_closes_anything(self):
        """掃描不完整時判定下架，會產生上千筆假的 closed 事件。"""
        existing = {"A": existing_row("A"), "B": existing_row("B")}
        r = classify({"A": job("A")}, existing, {}, {}, TODAY, complete=False)
        self.assertEqual(r.closed, [])
        self.assertNotIn("closed", [e["event_type"] for e in r.events])

    def test_complete_scan_closes_missing_jobs(self):
        existing = {"A": existing_row("A"), "B": existing_row("B")}
        r = classify({"A": job("A")}, existing, {}, {}, TODAY, complete=True)
        self.assertEqual(r.closed, ["B"])

    def test_changed_fields_emit_changed_event(self):
        existing = {"A": existing_row("A", salary_low=40000, apply_cnt=3)}
        seen = {"A": job("A", salary_low=50000, apply_cnt=9)}
        r = classify(seen, existing, {}, {}, TODAY, complete=True)

        changed = [e for e in r.events if e["event_type"] == "changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["payload"]["salary_low"], [40000, 50000])
        self.assertEqual(changed[0]["payload"]["apply_cnt"], [3, 9])

    def test_unchanged_job_updates_but_emits_no_event(self):
        existing = {"A": existing_row("A")}
        r = classify({"A": job("A")}, existing, {}, {}, TODAY, complete=True)
        self.assertEqual(r.events, [])
        self.assertEqual(len(r.update_rows), 1)


class TestNormalizeTitle(unittest.TestCase):

    def test_marketing_noise_is_stripped(self):
        base = normalize_title("半導體設備-電機維修工程師")
        for variant in ("半導體設備-電機維修工程師【年薪14~16個月/ 無經驗可】",
                        "半導體設備－電機維修工程師（急徵）",
                        "半導體設備 - 電機維修工程師 [南寮廠]"):
            self.assertEqual(normalize_title(variant), base, variant)

    def test_different_titles_do_not_collide(self):
        self.assertNotEqual(normalize_title("類比IC設計工程師"),
                            normalize_title("數位IC設計工程師"))

    def test_dedupe_key_requires_all_three_parts(self):
        a = make_dedupe_key("123", "硬體工程師", "6001006001")
        self.assertNotEqual(a, make_dedupe_key("999", "硬體工程師", "6001006001"))
        self.assertNotEqual(a, make_dedupe_key("123", "韌體工程師", "6001006001"))
        self.assertNotEqual(a, make_dedupe_key("123", "硬體工程師", "6001006002"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
