# -*- coding: utf-8 -*-
"""事件判定的回歸測試。這些情境錯了會造成不可逆的資料損壞。"""
import json
import unittest
from datetime import date

from scraper.diff import classify, assert_uniform_columns
from scraper.parse import normalize_title, make_dedupe_key, parse_list_job

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


def known_row(job_no, first_seen="2025-01-01", **kw):
    """資料庫裡存在但已下架的職缺（lookup_jobs 的回傳形狀）。"""
    row = {"job_no": job_no, "first_seen": first_seen, "is_open": False,
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
        """回鍋職缺必須寫回資料庫裡原本的 first_seen，不能變成今天。"""
        seen = {"A": job("A")}
        known = {"A": known_row("A", first_seen="2025-03-01")}
        r = classify(seen, {}, known, {}, TODAY, complete=True)

        self.assertEqual(r.reopened, {"A"})
        self.assertEqual(r.new_rows, [])
        self.assertEqual(r.update_rows[0]["first_seen"], "2025-03-01")
        self.assertEqual([e["event_type"] for e in r.events], ["reopened"])

    def test_every_row_carries_first_seen(self):
        """upsert 是 INSERT ... ON CONFLICT。任何一列缺 first_seen，
        只要它在資料庫不存在就會違反 NOT NULL，讓整批寫入失敗。"""
        existing = {"A": existing_row("A", first_seen="2026-01-01")}
        known = {"B": known_row("B", first_seen="2025-05-05")}
        seen = {"A": job("A"), "B": job("B"), "C": job("C")}
        r = classify(seen, existing, known, {}, TODAY, complete=True)

        self.assertEqual(len(r.new_rows) + len(r.update_rows), 3)
        for row in r.new_rows + r.update_rows:
            self.assertIsNotNone(row.get("first_seen"), row["job_no"])
        by_no = {x["job_no"]: x for x in r.new_rows + r.update_rows}
        self.assertEqual(by_no["A"]["first_seen"], "2026-01-01")   # 既有，保留
        self.assertEqual(by_no["B"]["first_seen"], "2025-05-05")   # 回鍋，保留
        self.assertEqual(by_no["C"]["first_seen"], "2026-09-07")   # 全新，今天

    def test_batches_have_uniform_columns(self):
        """同批 upsert 欄位集合不一致時，只出現在部分列的欄位會靜默寫不進去
        —— 例如只有重刊才有的 canonical_job_no。"""
        existing = {"A": existing_row("A")}
        known = {"B": known_row("B")}
        relist = job("D", cust="777")
        seen = {"A": job("A"), "B": job("B"), "C": job("C"), "D": relist}
        r = classify(seen, existing, known,
                     {relist["dedupe_key"]: "OLD"}, TODAY, complete=True)

        self.assertEqual(r.relisted, {"D"})
        assert_uniform_columns(r.new_rows, "new_rows")
        assert_uniform_columns(r.update_rows, "update_rows")

    def test_relisted_row_carries_canonical_pointer(self):
        relist = job("NEW")
        r = classify({"NEW": relist}, {}, {},
                     {relist["dedupe_key"]: "OLD"}, TODAY, complete=True)
        self.assertEqual(r.new_rows[0]["canonical_job_no"], "OLD")

    def test_plain_new_row_has_explicit_null_canonical(self):
        r = classify({"A": job("A")}, {}, {}, {}, TODAY, complete=True)
        self.assertIn("canonical_job_no", r.new_rows[0])
        self.assertIsNone(r.new_rows[0]["canonical_job_no"])

    def test_update_rows_drop_heavy_fields(self):
        """每天要更新上萬筆在架職缺。把 list_snapshot 與 description 一起重寫，
        單次 upsert 會超過 Supabase 的 statement timeout 而整批失敗。
        這兩個欄位只在首次出現時寫入，之後不再變動。"""
        heavy = {"list_snapshot": {"blob": "x" * 4000}, "description": "y" * 2000}
        existing = {"A": existing_row("A")}
        known = {"B": known_row("B")}
        seen = {"A": job("A", **heavy), "B": job("B", **heavy), "C": job("C", **heavy)}
        r = classify(seen, existing, known, {}, TODAY, complete=True)

        self.assertEqual(len(r.update_rows), 2)          # A 既有、B 回鍋
        for row in r.update_rows:
            self.assertNotIn("list_snapshot", row)
            self.assertNotIn("description", row)
            self.assertIn("last_seen", row)              # 該更新的仍在
            self.assertIn("apply_cnt", row)

        # 新職缺必須完整保存，那是唯一一次能存下原始快照的機會
        self.assertEqual(len(r.new_rows), 1)
        self.assertIn("list_snapshot", r.new_rows[0])
        self.assertIn("description", r.new_rows[0])

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


class TestParseListJob(unittest.TestCase):
    """列表解析。重點是輸出必須能直接丟進 Supabase。"""

    RAW = {
        "jobNo": "7790778",
        "jobName": "半導體設備工程師【年薪16個月】",
        "custNo": "13068501000",
        "custName": "測試半導體股份有限公司",
        "coIndustryDesc": "其他半導體相關業",
        "jobAddrNo": 6001006001,
        "jobAddrNoDesc": "新竹市",
        "jobAddress": "香山區香北路21號",
        "appearDate": "20260903",
        "applyCnt": 3,
        "salaryLow": 0,
        "salaryHigh": 0,
        "employeeCount": 70,
        "jobCat": [2008001005, 2009002007],
        "description": "工作內容...",
        "link": {"job": "https://www.104.com.tw/job/4mzei"},
    }

    def test_output_is_json_serializable(self):
        """寫入 Supabase 走 JSON。任何 date/datetime 物件都會在這裡爆掉。"""
        row = parse_list_job(self.RAW, TODAY)
        json.dumps(row)          # 不可拋例外

    def test_enc_id_extracted_from_link(self):
        """詳情頁只認網址短碼，用 jobNo 會 404。"""
        row = parse_list_job(self.RAW, TODAY)
        self.assertEqual(row["enc_id"], "4mzei")
        self.assertNotEqual(row["enc_id"], row["job_no"])

    def test_dates_are_iso_strings(self):
        row = parse_list_job(self.RAW, TODAY)
        self.assertEqual(row["appear_date"], "2026-09-03")
        self.assertEqual(row["last_seen"], "2026-09-07")
        self.assertIsInstance(row["appear_date"], str)

    def test_missing_appear_date_is_none(self):
        raw = dict(self.RAW)
        raw["appearDate"] = None
        self.assertIsNone(parse_list_job(raw, TODAY)["appear_date"])

    def test_job_cats_are_strings(self):
        row = parse_list_job(self.RAW, TODAY)
        self.assertEqual(row["job_cats"], ["2008001005", "2009002007"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
