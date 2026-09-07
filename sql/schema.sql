-- ============================================================
-- 104 求職追蹤 — Supabase schema
-- 在 Supabase SQL Editor 貼上執行即可（可重複執行）
-- ============================================================

-- ---------- 職缺主檔：一個職缺一列，每天只 UPDATE，不增列 ----------
create table if not exists jobs (
  job_no            text primary key,          -- 104 jobNo
  enc_id            text not null,             -- 網址上的短碼，抓詳情頁一定要用這個
  job_name          text not null,
  cust_no           text,                      -- 公司編號，重刊摺疊的關鍵
  cust_name         text,
  co_industry_desc  text,
  job_addr_no       text,
  job_addr_desc     text,
  job_address       text,
  appear_date       date,
  apply_cnt         integer,
  salary_low        integer,
  salary_high       integer,
  employee_count    integer,
  job_cats          text[],
  description       text,                      -- 列表就有完整 JD
  list_snapshot     jsonb,                     -- 最後一次列表原始資料
  detail            jsonb,                     -- 詳情頁快照：只抓一次，永久保存
  detail_fetched_at timestamptz,
  -- 以下自詳情頁抽出，方便前端直接篩選（列表頁沒有這些）
  salary_text       text,
  salary_min        integer,
  salary_max        integer,
  work_exp          text,
  edu               text,
  dedupe_key        text,                      -- cust_no|正規化職稱|地區
  canonical_job_no  text,                      -- 若為重刊，指向最早那一筆
  first_seen        date not null,
  last_seen         date not null,
  is_open           boolean not null default true,
  closed_at         date,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists jobs_first_seen_idx  on jobs (first_seen desc);
create index if not exists jobs_last_seen_idx   on jobs (last_seen desc);
create index if not exists jobs_dedupe_idx      on jobs (dedupe_key);
create index if not exists jobs_open_idx        on jobs (is_open) where is_open;
create index if not exists jobs_cust_idx        on jobs (cust_no);
create index if not exists jobs_detail_todo_idx on jobs (detail_fetched_at) where detail_fetched_at is null;

-- ---------- 事件流：只在狀態改變時 INSERT ----------
create table if not exists job_events (
  id         bigserial primary key,
  job_no     text not null references jobs(job_no) on delete cascade,
  event_type text not null check (event_type in
               ('discovered','relisted','closed','reopened','changed')),
  event_date date not null default current_date,
  payload    jsonb,
  created_at timestamptz not null default now()
);

create index if not exists job_events_job_idx  on job_events (job_no);
create index if not exists job_events_date_idx on job_events (event_date desc);
create index if not exists job_events_type_idx on job_events (event_type, event_date desc);

-- ---------- 你的標記 ----------
create table if not exists marks (
  job_no     text primary key references jobs(job_no) on delete cascade,
  status     text check (status in ('interested','applied','hidden')),
  note       text,
  updated_at timestamptz not null default now()
);

create table if not exists blocked_companies (
  cust_no    text primary key,
  cust_name  text,
  created_at timestamptz not null default now()
);

-- ---------- 搜尋條件：存 DB 不寫死，手機上可加減 ----------
create table if not exists queries (
  id       bigserial primary key,
  jobcat   text not null,
  area     text not null,
  label    text,
  enabled  boolean not null default true,
  unique (jobcat, area)
);

-- ---------- 執行記錄：沒有這張表就無法察覺爬蟲靜默死亡 ----------
create table if not exists runs (
  id              bigserial primary key,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  status          text not null default 'running'
                    check (status in ('running','ok','failed','blocked')),
  pages           integer default 0,
  requests        integer default 0,
  jobs_seen       integer default 0,
  new_jobs        integer default 0,
  relisted        integer default 0,
  closed          integer default 0,
  details_fetched integer default 0,
  warnings        jsonb,
  error           text
);

create index if not exists runs_started_idx on runs (started_at desc);

-- ---------- updated_at 自動維護 ----------
create or replace function touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists jobs_touch on jobs;
create trigger jobs_touch before update on jobs
  for each row execute function touch_updated_at();

drop trigger if exists marks_touch on marks;
create trigger marks_touch before update on marks
  for each row execute function touch_updated_at();

-- ============================================================
-- RLS：anon key 一定會出現在前端，沒開這段等於資料庫對全世界開放
-- 爬蟲用 service_role key，會自動繞過 RLS
-- ============================================================
alter table jobs              enable row level security;
alter table job_events        enable row level security;
alter table marks             enable row level security;
alter table blocked_companies enable row level security;
alter table queries           enable row level security;
alter table runs              enable row level security;

-- 登入後可讀
drop policy if exists jobs_read on jobs;
create policy jobs_read on jobs for select to authenticated using (true);

drop policy if exists job_events_read on job_events;
create policy job_events_read on job_events for select to authenticated using (true);

drop policy if exists runs_read on runs;
create policy runs_read on runs for select to authenticated using (true);

-- 登入後可讀寫（標記、封鎖公司、搜尋條件）
drop policy if exists marks_all on marks;
create policy marks_all on marks for all to authenticated
  using (true) with check (true);

drop policy if exists blocked_all on blocked_companies;
create policy blocked_all on blocked_companies for all to authenticated
  using (true) with check (true);

drop policy if exists queries_all on queries;
create policy queries_all on queries for all to authenticated
  using (true) with check (true);

-- 注意：anon 角色沒有任何 policy，因此未登入完全無法存取。
