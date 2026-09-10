from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import text

from ...db.audit import record as audit
from ...db.ids import new_ulid
from ...db.session import artifact_bucket, make_object_store
from ...db import storage
from ...services.run_service import Conflict, _Replay, submit_run
from ..deps import current_user, engine, issue_token, require_role
from ..schemas import (ArtifactOut, CreateRunRequest, ReviewOut, ReviewRequest,
                       RunAccepted, RunStatus, StepDetail, StepErrorOut,
                       StepSummary, TokenRequest, TokenResponse, UploadRequest,
                       UploadResponse)

router = APIRouter(prefix="/v1", tags=["runs"])


def _require_run(conn, run_id: str) -> None:
    """所有 /runs/{id}/* 子资源共用：run 不存在就 404，而不是返回空列表。
    空列表会让调用方分不清"没有这个 run"和"这个 run 还没产出"。"""
    if not conn.execute(text("select 1 from runs where run_id=:r"),
                        dict(r=run_id)).first():
        raise HTTPException(404, "run not found")


# ---------------------------------------------------------------- auth

@router.post("/auth/token", response_model=TokenResponse)
def auth_token(body: TokenRequest):
    """签发 token。这是个脚手架实现：不校验任何凭据。
    接生产身份源（OIDC / 内部 IdP）之前不能对外暴露。"""
    tok, ttl = issue_token(body.user_id, body.role)
    return {"access_token": tok, "token_type": "bearer", "expires_in": ttl}


# ---------------------------------------------------------------- runs

@router.post("/runs", response_model=RunAccepted, status_code=202)
def create_run(
    body: CreateRunRequest,
    response: Response,
    user: dict = Depends(require_role("submitter")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    route = "POST /v1/runs"
    try:
        with engine().begin() as conn:
            status, resp = submit_run(
                conn, user_id=user["id"], route=route,
                body=body.model_dump(), idem_key=idempotency_key,
            )
    except Conflict as e:
        raise HTTPException(409, str(e))
    except _Replay as r:
        status, resp = r.status, r.body
    response.status_code = status
    return resp


@router.get("/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: str, user: dict = Depends(current_user)):
    with engine().begin() as conn:
        row = conn.execute(
            text("""select r.run_id, r.run_status, r.current_step_id,
                           count(s.step_id) as steps_written
                    from runs r
                    left join step_records s on s.run_id = r.run_id
                    where r.run_id = :r
                    group by r.run_id"""),
            dict(r=run_id),
        ).mappings().first()
    if not row:
        raise HTTPException(404, "run not found")
    return dict(row)


# ---------------------------------------------------------------- steps

@router.get("/runs/{run_id}/steps", response_model=list[StepSummary])
def list_steps(run_id: str, user: dict = Depends(current_user)):
    with engine().begin() as conn:
        _require_run(conn, run_id)
        rows = conn.execute(
            text("""select step_id, step_order, step_status, started_at, finished_at
                    from step_records where run_id=:r order by step_order"""),
            dict(r=run_id)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}/steps/{step_id}", response_model=StepDetail)
def get_step(run_id: str, step_id: str, user: dict = Depends(current_user)):
    with engine().begin() as conn:
        _require_run(conn, run_id)
        row = conn.execute(
            text("""select step_id, step_status, schema_version, output_payload
                    from step_records where run_id=:r and step_id=:s"""),
            dict(r=run_id, s=step_id)).mappings().first()
    if not row:
        raise HTTPException(404, "step not found for this run")
    return dict(row)


# ---------------------------------------------------------------- artifacts

@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(run_id: str, user: dict = Depends(current_user)):
    store = make_object_store()
    with engine().begin() as conn:
        _require_run(conn, run_id)
        rows = conn.execute(
            text("""select artifact_id, artifact_type, producing_step_id,
                           upload_status, size_bytes, bucket, object_key
                    from artifacts where run_id=:r order by created_at"""),
            dict(r=run_id)).mappings().all()
    out = []
    for r in rows:
        d = {k: r[k] for k in ("artifact_id", "artifact_type", "producing_step_id",
                               "upload_status", "size_bytes")}
        # 只有 available 才给下载链接 —— pending/failed 的对象可能压根不存在
        d["download_url"] = (storage.presigned_url(store, r["bucket"], r["object_key"])
                             if r["upload_status"] == "available" else None)
        out.append(d)
    return out


@router.post("/runs/{run_id}/uploads", response_model=UploadResponse, status_code=201)
def request_upload(run_id: str, body: UploadRequest,
                   user: dict = Depends(require_role("submitter"))):
    """签发一个上传用的 URL。此时还不建 artifacts 行 —— 客户端上传完成后
    由 Step 1 写入器登记（register_pending -> mark_available）。"""
    with engine().begin() as conn:
        _require_run(conn, run_id)
    bucket = artifact_bucket()
    key = storage.object_key(run_id, "step_01_intake", body.filename)
    store = make_object_store()
    url = store.generate_presigned_url("put_object",
                                       Params={"Bucket": bucket, "Key": key},
                                       ExpiresIn=900)
    return {"upload_url": url, "object_key": key, "bucket": bucket}


# ---------------------------------------------------------------- errors

@router.get("/runs/{run_id}/errors", response_model=list[StepErrorOut])
def list_errors(run_id: str, user: dict = Depends(current_user)):
    with engine().begin() as conn:
        _require_run(conn, run_id)
        rows = conn.execute(
            text("""select step_error_id, step_id, error_type, error_message, retry_count
                    from step_errors where run_id=:r order by created_at"""),
            dict(r=run_id)).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- reviews

@router.get("/runs/{run_id}/reviews", response_model=list[ReviewOut])
def list_reviews(run_id: str, user: dict = Depends(current_user)):
    with engine().begin() as conn:
        _require_run(conn, run_id)
        rows = conn.execute(
            text("""select human_review_id, candidate_id, review_status,
                           reviewer_id, decision, notes
                    from human_reviews where run_id=:r order by created_at"""),
            dict(r=run_id)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/runs/{run_id}/reviews", response_model=ReviewOut)
def submit_review(run_id: str, body: ReviewRequest,
                  user: dict = Depends(require_role("reviewer", "approver"))):
    """人工复核决定。只有 run 停在 awaiting_review 时才接受 —— 对已经
    终结的 run 补一条复核记录没有意义，且会让审计轨迹自相矛盾。"""
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision 必须是 approve 或 reject")

    with engine().begin() as conn:
        row = conn.execute(text("select run_status from runs where run_id=:r"),
                           dict(r=run_id)).mappings().first()
        if not row:
            raise HTTPException(404, "run not found")
        if row["run_status"] != "awaiting_review":
            raise HTTPException(409, f"run 当前是 {row['run_status']}，不处于待复核状态")

        rid = new_ulid("rev")
        conn.execute(
            text("""insert into human_reviews (human_review_id, run_id, candidate_id,
                      review_status, reviewer_id, decision, notes, reviewed_at)
                    values (:id, :r, :cid, 'completed', :uid, :dec, :notes, now())"""),
            dict(id=rid, r=run_id, cid=body.candidate_id, uid=user["id"],
                 dec=body.decision, notes=body.notes))

        # approve -> 放行（这里直接终结；接上重新入队是后续工作）
        # reject  -> 终止
        new_status = "completed" if body.decision == "approve" else "failed"
        conn.execute(
            text("""update runs set run_status=:s, completed_at=now(), updated_at=now()
                    where run_id=:r"""), dict(s=new_status, r=run_id))
        audit(conn, run_id, f"review.{body.decision}", actor=user["id"],
              target_type="run", target_id=run_id,
              payload={"human_review_id": rid, "notes": body.notes})

        out = conn.execute(
            text("""select human_review_id, candidate_id, review_status,
                           reviewer_id, decision, notes
                    from human_reviews where human_review_id=:id"""),
            dict(id=rid)).mappings().first()
    return dict(out)
