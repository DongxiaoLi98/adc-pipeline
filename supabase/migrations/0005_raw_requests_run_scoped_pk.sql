-- 0005 — raw_requests: 全局主键 -> run 内作用域的复合主键
--
-- 问题：raw_request_record_id 来自上游 payload，按 ADR-5 只在 run 内唯一，
-- 但 0001 把它建成了全局主键。两个 run 都产出 "rr_1" 时第二个直接主键冲突。
-- candidates / materials / uploaded_files 早已是 (run_id, <id>) 复合键，
-- 这里把 raw_requests 对齐到同一约定。
--
-- 应用层无需改动：两张子表本来就带 run_id，插入语句已经提供了这一列。
--
-- 回滚见文件末尾注释。

begin;

-- 1. 先摘掉引用旧主键的外键
alter table uploaded_files
  drop constraint uploaded_files_raw_request_record_id_fkey;

alter table structured_queries
  drop constraint structured_queries_raw_request_record_id_fkey;

-- 2. 换主键。unique(run_id) 保留 —— 它继续保证"每个 run 一条 raw request"，
--    复合主键则允许 rr_1 在不同 run 里重复出现。
alter table raw_requests
  drop constraint raw_requests_pkey;

alter table raw_requests
  add constraint raw_requests_pkey
  primary key (run_id, raw_request_record_id);

-- 3. 重建为复合外键。两列都非空时才生效（MATCH SIMPLE），
--    所以 raw_request_record_id 为 NULL 的行仍然合法。
alter table uploaded_files
  add constraint uploaded_files_raw_request_fkey
  foreign key (run_id, raw_request_record_id)
  references raw_requests (run_id, raw_request_record_id)
  on delete cascade;

alter table structured_queries
  add constraint structured_queries_raw_request_fkey
  foreign key (run_id, raw_request_record_id)
  references raw_requests (run_id, raw_request_record_id);

-- 4. 复合外键需要匹配的索引才能高效校验/级联
create index if not exists idx_uploaded_files_raw_request
  on uploaded_files (run_id, raw_request_record_id);

create index if not exists idx_structured_queries_raw_request
  on structured_queries (run_id, raw_request_record_id);

commit;

-- 回滚（仅在没有跨 run 重复 id 时可行）：
--   alter table uploaded_files drop constraint uploaded_files_raw_request_fkey;
--   alter table structured_queries drop constraint structured_queries_raw_request_fkey;
--   alter table raw_requests drop constraint raw_requests_pkey;
--   alter table raw_requests add constraint raw_requests_pkey primary key (raw_request_record_id);
--   ... 再按 0001 的定义重建两条单列外键
