-- ============================================================
-- 前端用的檢視表
-- 在 Supabase SQL Editor 貼上執行（可重複執行）
-- ============================================================

-- 清單用：刻意排除 description / detail / list_snapshot 三個大欄位。
-- 一筆職缺的 JD 動輒上千字，11,000 筆全載進手機會非常慢，
-- 詳細內容改在展開單一職缺時才查。
--
-- security_invoker = true 讓這個 view 沿用呼叫者的權限，
-- 底層 jobs / marks / blocked_companies 的 RLS 才會生效。
-- 沒有這個設定的話，view 會以建立者（postgres）的權限執行，
-- 等於在 RLS 上開一個後門。
create or replace view v_job_list
with (security_invoker = true) as
select
  j.job_no,
  j.enc_id,
  j.job_name,
  j.cust_no,
  j.cust_name,
  j.co_industry_desc,
  j.job_addr_desc,
  j.job_address,
  j.employee_count,
  j.appear_date,
  j.apply_cnt,
  j.salary_text,
  j.salary_min,
  j.salary_max,
  j.work_exp,
  j.edu,
  j.job_cats,
  j.first_seen,
  j.last_seen,
  j.is_open,
  j.closed_at,
  j.canonical_job_no,
  (j.detail_fetched_at is not null)              as has_detail,
  (current_date - j.first_seen)                  as days_tracked,
  m.status                                       as mark_status,
  m.note                                         as mark_note,
  (b.cust_no is not null)                        as company_blocked
from jobs j
left join marks m             on m.job_no  = j.job_no
left join blocked_companies b on b.cust_no = j.cust_no;

grant select on v_job_list to authenticated;

-- 每日統計：給前端首頁顯示「今天有什麼變化」。
-- 重刊（canonical_job_no 不為 null）不算新增，跟推播的口徑一致。
create or replace view v_daily_stats
with (security_invoker = true) as
select
  event_date,
  count(*) filter (where event_type = 'discovered') as discovered,
  count(*) filter (where event_type = 'relisted')   as relisted,
  count(*) filter (where event_type = 'reopened')   as reopened,
  count(*) filter (where event_type = 'changed')    as changed,
  count(*) filter (where event_type = 'closed')     as closed
from job_events
group by event_date
order by event_date desc;

grant select on v_daily_stats to authenticated;

-- 搜尋用索引。前端的關鍵字搜尋會對職稱與公司名做 ILIKE，
-- 沒有索引的話 11,000 筆每次都全表掃描。
create extension if not exists pg_trgm;
create index if not exists jobs_name_trgm  on jobs using gin (job_name gin_trgm_ops);
create index if not exists jobs_cust_trgm  on jobs using gin (cust_name gin_trgm_ops);
create index if not exists jobs_canonical_idx on jobs (canonical_job_no);
