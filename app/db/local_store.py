"""本地文件系统对象存储 —— 实现 boto3 S3 客户端的子集接口。

为什么是"实现接口"而不是"加个 if 分支"：
store_artifact 原本写着 `etag = None if backend == "local" else put_object(...)`，
也就是 local 模式下**根本不写字节**，只往 artifacts 表插一条指针。dev 环境里
"pending -> available -> 可下载"这条生命周期是假的，`ARTIFACT_BASE_URI` 这个
配置项也从没被任何代码读过。

让本地实现长得和 boto3 客户端一样，调用方就不需要知道自己在跟谁说话 ——
和 session.make_object_store() 用 endpoint_url 区分 Supabase/LocalStack/S3
是同一个思路（docs/00 的可移植性纪律：只通过 S3 API 访问对象存储）。

字节落在 ARTIFACT_LOCAL_ROOT（默认 ./storage，已在 .gitignore 里）。
"""
from __future__ import annotations

import hashlib
import os
import pathlib


def local_root() -> pathlib.Path:
    return pathlib.Path(os.getenv("ARTIFACT_LOCAL_ROOT", "storage")).resolve()


class LocalObjectStore:
    """put_object / get_object / generate_presigned_url 的文件系统实现。

    参数命名刻意跟 boto3 保持一致（Bucket / Key / Body），这样
    storage.py 里的调用代码对两种后端完全相同。
    """

    def __init__(self, root: pathlib.Path | None = None):
        self.root = root or local_root()

    def _path(self, bucket: str, key: str) -> pathlib.Path:
        # object_key() 里的 filename 来自上游 payload，可能含 ../ ——
        # 解析后必须仍落在 root 内，否则就是路径穿越。
        p = (self.root / bucket / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"object key 越出存储根目录: {key!r}")
        return p

    def put_object(self, *, Bucket: str, Key: str, Body: bytes,
                   ContentType: str | None = None) -> dict:
        p = self._path(Bucket, Key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(Body)
        # S3 对非分块上传返回内容的 MD5 作为 ETag，这里保持一致
        return {"ETag": '"%s"' % hashlib.md5(Body).hexdigest()}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        p = self._path(Bucket, Key)
        if not p.is_file():
            raise FileNotFoundError(f"对象不存在: {Bucket}/{Key}")
        return {"Body": p.read_bytes()}

    def generate_presigned_url(self, operation: str = "get_object", *, Params: dict,
                               ExpiresIn: int = 3600) -> str:
        """本地没有签名概念，get/put 都返回同一个 file:// URL。

        注意这跟 S3 的预签名 URL 有本质区别：没有过期时间、没有访问控制。
        仅供本地调试，任何"预签名 URL 的安全性"结论都不能从这里得出。
        """
        return self._path(Params["Bucket"], Params["Key"]).as_uri()
