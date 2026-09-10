# 03 — AWS Migration Roadmap

Kept off the critical path until an AWS account exists. The design already maps
onto AWS; migration is configuration + data sync, not a redesign.

## Mapping

| Current (dev) | AWS | Role |
|---|---|---|
| Supabase Local / PostgreSQL | **RDS for PostgreSQL** | primary operational DB |
| Supabase Storage / LocalStack (`s3://`) | **Amazon S3** | artifacts, tool outputs, reports |
| Parquet exports + DuckDB | **Athena + Glue** | historical analytics |
| (Postgres covers it) | **DynamoDB** (optional) | fast run-state read model |

Docs: RDS PostgreSQL `https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html`,
S3 `https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html`,
Athena `https://docs.aws.amazon.com/athena/latest/ug/what-is.html`,
Glue `https://docs.aws.amazon.com/glue/latest/dg/what-is-glue.html`,
DynamoDB `https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Introduction.html`.

## Cutover steps (when approved)

1. **DB** — point `DATABASE_URL` at RDS; replay `supabase/migrations/*.sql` unchanged.
2. **Storage** — set `env/aws.env` (`ARTIFACT_STORAGE_BACKEND=aws_s3`, unset
   `S3_ENDPOINT_URL`); sync existing objects to the S3 bucket. Application code
   is unchanged — same boto3 client.
3. **Analytics** — run a Glue crawler over the partitioned Parquet prefix
   (`runs/run_id=.../step=.../`) so Athena queries it with the same SQL used in
   DuckDB locally.
4. **(Optional) DynamoDB** — mirror `runs` + latest `step_records` status into a
   Dynamo table (PK=`RUN#<run_id>`, SK=`STATUS` / `STEP#<step_id>`) if the UI
   needs low-latency status reads. `step_records` is a self-contained JSONB
   document, so no remodeling is required.

## Analytics layout (set up now, pays off later)

Export historical outputs to partitioned Parquet from day one:

```
s3://adc-analytics/runs/date=YYYY-MM-DD/step=step_06_developability/part-*.parquet
```

Partition by date/step + Parquet + compression is what keeps a future Athena
scan cheap. Query locally today with DuckDB; the SQL shape is identical to
Athena later.

## Forward-looking tables (Steps 7/13/14, deferred)

`scores` (use `NUMERIC`, not float, for anything that drives ordering),
`rankings`, `ip_record`. Reserve their shape when those steps land; adding them
is additive, not a migration of Steps 1–6.
