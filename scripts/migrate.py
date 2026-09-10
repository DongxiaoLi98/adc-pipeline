#!/usr/bin/env python3
"""迁移 runner —— 幂等地应用 supabase/migrations/*.sql。

为什么需要它：迁移文件本身是纯 SQL（ADR-1：可原样 replay 到 RDS），但
`psql -f` 不知道哪些已经跑过。重复执行只能靠报错兜底，而 AWS 上迁移是
ECS 一次性 task，重试是常态，必须幂等。

台账表 schema_migrations 记录 version + 文件 sha256：
  * 已应用的跳过
  * 已应用的文件事后被改动 -> 报错退出（schema 漂移，静默跑过去更危险）

每个文件在**一个事务**里执行，台账写入也在同一事务 —— 迁移失败不会留下
"跑了一半但台账说没跑"的状态。因此迁移文件自身不应包含 begin/commit，
runner 会剥离并提示。

用法：
    python scripts/migrate.py                # 应用所有未应用的迁移
    python scripts/migrate.py --status       # 只看状态，不改库
    python scripts/migrate.py --baseline     # 把现有文件全部标记为已应用（不执行）
                                             # 用于已经手工 psql -f 跑过的库

DATABASE_URL 必须已设置。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import pathlib

from sqlalchemy import create_engine, text

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations"

LEDGER_DDL = """
create table if not exists schema_migrations (
  version    text primary key,
  filename   text not null,
  checksum   text not null,
  applied_at timestamptz not null default now()
)
"""

_TX_STMT = re.compile(r"^\s*(begin|commit|end)\s*;\s*$", re.I | re.M)


def engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL 未设置")
    # psycopg 的 URL 直接用；runner 自己管事务
    return create_engine(url, future=True)


def discover() -> list[tuple[str, pathlib.Path, str]]:
    """返回 [(version, path, checksum)]，按文件名排序。"""
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = p.name.split("_", 1)[0]
        body = p.read_bytes()
        out.append((version, p, hashlib.sha256(body).hexdigest()))
    return out


def applied(conn) -> dict[str, str]:
    rows = conn.execute(text("select version, checksum from schema_migrations")).all()
    return {r.version: r.checksum for r in rows}


def strip_tx(sql: str, filename: str) -> str:
    """剥离文件自带的 begin/commit —— runner 提供事务边界。"""
    if _TX_STMT.search(sql):
        print(f"    提示: {filename} 自带 begin/commit，已剥离（runner 统一管事务）")
    return _TX_STMT.sub("", sql)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只显示状态")
    ap.add_argument("--check", action="store_true",
                    help="有待应用的迁移则以非零退出（CI 用，不产生管道）")
    ap.add_argument("--baseline", action="store_true",
                    help="把所有迁移标记为已应用但不执行（用于已手工跑过的库）")
    args = ap.parse_args()

    eng = engine()
    with eng.begin() as conn:
        conn.execute(text(LEDGER_DDL))

    files = discover()
    if not files:
        sys.exit(f"{MIGRATIONS_DIR} 下没有 .sql 文件")

    with eng.connect() as conn:
        done = applied(conn)

    # 漂移检测：已应用的文件内容变了
    drift = [(v, p.name) for v, p, c in files
             if v in done and done[v] != c]
    if drift and not args.baseline:
        print("检测到 schema 漂移 —— 以下迁移已应用但文件内容已改动：")
        for v, name in drift:
            print(f"  {v}  {name}")
        sys.exit("请新建一个迁移来修改 schema，不要改已应用的文件。")

    pending = [(v, p, c) for v, p, c in files if v not in done]

    if args.check:
        if pending:
            print(f"有 {len(pending)} 个迁移待应用")
            sys.exit(1)
        print(f"迁移已是最新（{len(done)} 个）")
        return

    if args.status:
        print(f"台账中已应用: {len(done)}    待应用: {len(pending)}")
        for v, p, c in files:
            mark = "已应用" if v in done else "待应用"
            print(f"  [{mark}] {p.name}")
        return

    if args.baseline:
        with eng.begin() as conn:
            for v, p, c in files:
                conn.execute(
                    text("""insert into schema_migrations (version, filename, checksum)
                            values (:v, :f, :c)
                            on conflict (version) do update
                              set checksum = excluded.checksum,
                                  filename = excluded.filename"""),
                    dict(v=v, f=p.name, c=c))
        print(f"已把 {len(files)} 个迁移标记为已应用（未执行任何 DDL）")
        return

    if not pending:
        print(f"没有待应用的迁移（台账中 {len(done)} 个）")
        return

    for v, p, c in pending:
        print(f"应用 {p.name} ...")
        sql = strip_tx(p.read_text(), p.name)
        try:
            with eng.begin() as conn:            # 迁移 + 台账写入同一事务
                conn.execute(text(sql))
                conn.execute(
                    text("""insert into schema_migrations (version, filename, checksum)
                            values (:v, :f, :c)"""),
                    dict(v=v, f=p.name, c=c))
        except Exception as e:
            print(f"    失败，已回滚: {str(e)[:300]}")
            sys.exit(1)
        print("    ok")

    print(f"完成，应用了 {len(pending)} 个迁移")


if __name__ == "__main__":
    main()
