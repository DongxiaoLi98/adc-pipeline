-- 0006 — step_records.step_status: 执行状态与业务状态解耦
--
-- 问题：step_status 本该记录"这条 step_record 写成功了吗"，实际却混进了
-- 上游 payload 里的业务状态。生产数据里出现过 5 种取值：
--   completed / succeeded   —— 同义，两套词汇（写入器 vs worker）
--   prepared                —— Step 7 的 package_status 泄漏
--   not_run                 —— Step 9 的 runtime_execution_mode 泄漏（且写入其实成功了）
--   partial 等              —— Step 6 的 prefilter_status 泄漏
--
-- "not_run" 尤其危险：step_record 明明写成功了，监控查询却会把它算成没跑。
--
-- 这些业务状态在各自的投影表里都已有专属列
-- （prepared_structure_packages.package_status、structure_prediction_summaries.summary_status、
--  liability_summaries.prefilter_status、variant_design_summaries.runtime_execution_mode），
-- 所以从 step_status 剥离不丢任何信息。
--
-- 冻结后的词汇（对应 ADR-4 的 "freeze-then-constrain"）：
--   running | succeeded | failed | blocked
--
-- blocked 保留给 Step 3：readiness 判定有 blocking_reasons 时，步骤本身执行成功
-- 但流水线不应继续，这是一个真实的执行态，不是业务字段。

begin;

-- 1. 归一化历史数据。除 failed / blocked / running 外，其余一律视为写入成功
--    —— 这些行本来就是 write_step_record 正常返回后落的盘。
update step_records
   set step_status = 'succeeded'
 where step_status not in ('running', 'succeeded', 'failed', 'blocked');

-- 2. 冻结词汇
alter table step_records
  add constraint chk_step_records_step_status
  check (step_status in ('running', 'succeeded', 'failed', 'blocked'));

commit;

-- 回滚：
--   alter table step_records drop constraint chk_step_records_step_status;
--   （数据归一化不可逆——原始业务状态请从对应投影表或 output_payload 读取）
