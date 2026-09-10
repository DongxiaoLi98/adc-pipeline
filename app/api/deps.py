"""FastAPI 依赖：数据库连接 + 认证/RBAC。

认证有两种模式：
  * 配置了 AUTH_JWT_SECRET  -> 校验 HS256 JWT（生产模式）
  * 未配置                  -> 退回开发用的明文 token，并在启动时打警告

之所以保留降级模式：本地开发和集成测试不需要起一个认证服务。之所以要打警告：
原先四个明文 token 是硬编码的、且 dev-admin 能绕过所有 RBAC —— 那种东西
一旦跟着镜像上云就是个后门。现在必须显式不配密钥才会启用，而且会喊。
"""
from __future__ import annotations

import os
import time
import warnings

import jwt
from fastapi import Depends, Header, HTTPException

from ..db.session import make_engine

_ENGINE = None

ROLES = ("submitter", "approver", "reviewer", "viewer", "admin")
JWT_ALG = "HS256"
TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "3600"))


def engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine()
    return _ENGINE


def jwt_secret() -> str | None:
    return os.getenv("AUTH_JWT_SECRET") or None


# 仅在未配置 AUTH_JWT_SECRET 时启用
_DEV_TOKENS = {
    "dev-submitter": ("u_submitter", "submitter"),
    "dev-approver": ("u_approver", "approver"),
    "dev-reviewer": ("u_reviewer", "reviewer"),
    "dev-viewer": ("u_viewer", "viewer"),
    "dev-admin": ("u_admin", "admin"),
}

if not jwt_secret():
    warnings.warn(
        "AUTH_JWT_SECRET 未配置 —— 正在使用开发用明文 token。"
        "任何联网环境都必须配置密钥。", RuntimeWarning, stacklevel=2)


def issue_token(user_id: str, role: str) -> tuple[str, int]:
    """签发 token。返回 (token, 有效秒数)。"""
    if role not in ROLES:
        raise HTTPException(422, f"role 必须是 {ROLES} 之一")
    secret = jwt_secret()
    if not secret:
        for tok, (uid, r) in _DEV_TOKENS.items():   # 降级模式：返回对应的开发 token
            if r == role:
                return tok, TOKEN_TTL_SECONDS
        raise HTTPException(422, "该角色没有对应的开发 token")
    now = int(time.time())
    payload = {"sub": user_id, "role": role, "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, secret, algorithm=JWT_ALG), TOKEN_TTL_SECONDS


def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "unauthenticated")

    secret = jwt_secret()
    if not secret:
        if token not in _DEV_TOKENS:
            raise HTTPException(401, "unauthenticated")
        uid, role = _DEV_TOKENS[token]
        return {"id": uid, "role": role}

    try:
        claims = jwt.decode(token, secret, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "unauthenticated")
    return {"id": claims["sub"], "role": claims.get("role", "viewer")}


def require_role(*roles: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin" and user["role"] not in roles:
            raise HTTPException(403, "forbidden")
        return user
    return dep
