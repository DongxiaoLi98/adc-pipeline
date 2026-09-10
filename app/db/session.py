"""Connections. The ONLY place that knows which backend we're pointed at.

Switching between Supabase Local, LocalStack, and AWS is a matter of env vars
(see env/*.env) — not a code change. The DB is plain PostgreSQL either way;
the object store is spoken to purely through the S3 API, so the client is
identical and only ``endpoint_url`` differs (unset it for real AWS S3).
"""
from __future__ import annotations

import os

import boto3
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def make_engine() -> Engine:
    url = os.environ["DATABASE_URL"]  # e.g. postgresql+psycopg://postgres:postgres@localhost:54322/postgres
    return create_engine(url, pool_pre_ping=True, future=True)


def storage_backend() -> str:
    return os.getenv("ARTIFACT_STORAGE_BACKEND", "local")


def make_object_store():
    """对象存储客户端。

    local        -> 本地文件系统（接口与 boto3 一致，见 local_store.py）
    其余后端      -> boto3 S3 客户端；endpoint_url 指向 Supabase Storage /
                    LocalStack，真实 AWS S3 时留空由 boto3 自行解析。
    """
    if storage_backend() == "local":
        from .local_store import LocalObjectStore
        return LocalObjectStore()
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        region_name=os.getenv("S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
    )


def artifact_bucket() -> str:
    return os.getenv("S3_BUCKET", "adc-pipeline-local")
