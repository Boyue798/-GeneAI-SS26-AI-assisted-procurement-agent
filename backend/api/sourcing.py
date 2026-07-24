"""
Sourcing API — supplier discovery.

Optimization over the original:
  - Added auth protection via Depends(get_current_user) so the endpoint
    requires a valid Bearer token, matching the openapi.yaml contract.
  - Added asynchronous search jobs so long-running live web research can expose
    real progress to the frontend instead of looking frozen behind one request.
  - Auto-saves search results to Supabase (new suppliers marked origin=web).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from typing import Annotated, AsyncIterator, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import AuthUser, get_current_user
from api.agent_compat import supported_kwargs
from db_writer import save_sourcing_request_and_suppliers  # 新加的 import


router = APIRouter(prefix="/api/sourcing", tags=["sourcing"])


class StructuredFields(BaseModel):
    productName: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    specifications: Optional[str] = None
    standards: Optional[str] = None
    category: Optional[str] = None
    country: Optional[str] = None
    targetRegion: Optional[str] = None
    certifications: Optional[str] = None
    minOrderQuantity: Optional[str] = None
    qualityRequirements: Optional[str] = None
    environmentalRequirements: Optional[str] = None


class EvaluationCriterion(BaseModel):
    """One user-configurable dimension for supplier recommendation ranking."""

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: Optional[str] = Field(default=None, max_length=120)
    weight: float = Field(ge=0, le=100)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    structured: Optional[StructuredFields] = None
    criteria: list[EvaluationCriterion] = Field(default_factory=list, max_length=12)


class SearchJobEvent(BaseModel):
    timestamp: int
    phase: str
    message: str
    progress: int


class SearchJobResponse(BaseModel):
    jobId: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: int
    step: str
    events: list[SearchJobEvent]
    intent: dict | None = None
    results: list[dict] = Field(default_factory=list)
    error: str | None = None


class _SearchJobState(SearchJobResponse):
    owner: str


_SEARCH_JOBS: dict[str, _SearchJobState] = {}
_MAX_JOBS = 100
_SEARCH_RESULT_CACHE: dict[str, tuple[float, dict]] = {}
_MAX_CACHE_ENTRIES = 200


def _now_ms() -> int:
    return int(time.time() * 1000)


def _public_job(job: _SearchJobState) -> SearchJobResponse:
    return SearchJobResponse(**job.model_dump(exclude={"owner"}))


def _append_event(job: _SearchJobState, phase: str, message: str, progress: int) -> None:
    progress = max(0, min(100, int(progress)))
    job.progress = max(job.progress, progress)
    job.step = message
    job.events.append(
        SearchJobEvent(
            timestamp=_now_ms(),
            phase=phase,
            message=message,
            progress=job.progress,
        )
    )


def _prune_jobs() -> None:
    if len(_SEARCH_JOBS) <= _MAX_JOBS:
        return
    oldest = sorted(
        _SEARCH_JOBS.items(),
        key=lambda item: item[1].events[0].timestamp if item[1].events else 0,
    )
    for job_id, _ in oldest[: len(_SEARCH_JOBS) - _MAX_JOBS]:
        _SEARCH_JOBS.pop(job_id, None)


def _cache_ttl_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("SOURCING_CACHE_TTL_SECONDS", "300")))
    except ValueError:
        return 300.0


def _search_cache_key(owner: str, query: str, structured: Optional[dict], criteria: Optional[list[dict]]) -> str:
    payload = {
        "owner": owner,
        "query": query.strip(),
        "structured": structured or {},
        "criteria": criteria or [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _get_cached_result(cache_key: str) -> dict | None:
    entry = _SEARCH_RESULT_CACHE.get(cache_key)
    if entry is None:
        return None
    created_at, result = entry
    if _cache_ttl_seconds() <= 0 or time.monotonic() - created_at > _cache_ttl_seconds():
        _SEARCH_RESULT_CACHE.pop(cache_key, None)
        return None
    return copy.deepcopy(result)


def _put_cached_result(cache_key: str, result: dict) -> None:
    if _cache_ttl_seconds() <= 0:
        return
    if len(_SEARCH_RESULT_CACHE) >= _MAX_CACHE_ENTRIES:
        oldest = min(_SEARCH_RESULT_CACHE, key=lambda key: _SEARCH_RESULT_CACHE[key][0])
        _SEARCH_RESULT_CACHE.pop(oldest, None)
    _SEARCH_RESULT_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(result))


def clear_search_result_cache() -> None:
    """Invalidate cached sourcing results after a local supplier-data change."""
    _SEARCH_RESULT_CACHE.clear()


async def _run_search_job(
    job_id: str,
    query: str,
    agent: object,
    structured: Optional[dict] = None,
    criteria: Optional[list[dict]] = None,
    cache_key: str | None = None,
) -> None:
    job = _SEARCH_JOBS[job_id]
    job.status = "running"
    _append_event(job, "queued", "已接收供应商研究任务，Agent 正在启动采购需求分析流程...", 5)

    def progress(phase: str, message: str, percent: int) -> None:
        _append_event(job, phase, message, percent)

    try:
        cached = _get_cached_result(cache_key) if cache_key else None
        if cached is not None:
            job.intent = cached.get("intent")
            job.results = cached.get("results", [])
            job.status = "completed"
            _append_event(job, "cache", "已命中近期相同需求的结果缓存，正在恢复候选名单…", 92)
            _append_event(job, "completed", "候选名单已准备就绪，可以开始查看结果了。", 100)
            try:
                save_sourcing_request_and_suppliers(
                    request_text=query,
                    requested_by=job.owner,
                    suppliers=job.results,
                )
            except Exception as e:
                print(f"[sourcing] 保存数据库失败，不影响缓存结果返回: {e}")
            return
        agent_method = agent.search_suppliers  # type: ignore[attr-defined]
        result = await agent_method(
            query,
            **supported_kwargs(
                agent_method,
                {"progress": progress, "structured": structured, "criteria": criteria},
            ),
        )
        job.intent = result.get("intent")
        job.results = result.get("results", [])
        # An empty result is often caused by a transient web-search outage.
        # Do not turn it into a five-minute "no data" response for retries.
        if cache_key and result.get("results"):
            _put_cached_result(cache_key, result)
        job.status = "completed"
        _append_event(job, "completed", "候选名单已准备就绪，可以开始查看结果了。", 100)
        # 自动把这次搜索结果存进数据库（新供应商 origin=web）
        try:
            save_sourcing_request_and_suppliers(
                request_text=query,
                requested_by=job.owner,
                suppliers=job.results,
            )
        except Exception as e:
            print(f"[sourcing] 保存数据库失败，不影响返回结果: {e}")
    except Exception as exc:  # pragma: no cover - exact production errors vary
        job.status = "failed"
        job.error = str(exc)
        _append_event(job, "failed", f"研究过程遇到了问题：{exc}。请尝试调整需求描述后重试。", max(job.progress, 5))


def reset_search_jobs_for_tests() -> None:
    _SEARCH_JOBS.clear()
    clear_search_result_cache()


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_search_job_events(job_id: str, owner: str) -> AsyncIterator[str]:
    """Yield job updates as Server-Sent Events until the job reaches a terminal state."""
    last_event_count = -1
    last_status: str | None = None
    while True:
        job = _SEARCH_JOBS.get(job_id)
        if job is None or job.owner != owner:
            yield _format_sse("error", {"code": "NOT_FOUND", "message": "Search job not found"})
            return

        event_count = len(job.events)
        if event_count != last_event_count or job.status != last_status:
            yield _format_sse("job", _public_job(job).model_dump())
            last_event_count = event_count
            last_status = job.status

        if job.status in {"completed", "failed"}:
            yield _format_sse("done", _public_job(job).model_dump())
            return

        await asyncio.sleep(1)


@router.post("/search")
async def search(
    req: SearchRequest,
    request: Request,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
):
    """
    POST /api/sourcing/search — match openapi.yaml spec.

    Protected: requires Authorization: Bearer *** header.
    """
    structured = req.structured.model_dump() if req.structured else None
    criteria = [criterion.model_dump() for criterion in req.criteria]
    cache_key = _search_cache_key(current_user.email, req.query, structured, criteria)
    cached = _get_cached_result(cache_key)
    if cached is not None:
        try:
            save_sourcing_request_and_suppliers(
                request_text=req.query,
                requested_by=current_user.email,
                suppliers=cached.get("results", []),
            )
        except Exception as e:
            print(f"[sourcing] 保存数据库失败，不影响缓存结果返回: {e}")
        return cached
    agent_method = request.app.state.agent.search_suppliers
    result = await agent_method(
        req.query,
        **supported_kwargs(
            agent_method,
            {"structured": structured, "criteria": criteria},
        ),
    )
    # Keep successful candidate lists short-lived, but let an empty search be
    # retried because external supplier sources may recover moments later.
    if result.get("results"):
        _put_cached_result(cache_key, result)

    # 自动把这次搜索结果存进数据库（新供应商 origin=web）
    try:
        save_sourcing_request_and_suppliers(
            request_text=req.query,
            requested_by=current_user.email,
            suppliers=result.get("results", []),
        )
    except Exception as e:
        print(f"[sourcing] 保存数据库失败，不影响返回结果: {e}")

    return result


@router.post("/search-jobs", response_model=SearchJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_search_job(
    req: SearchRequest,
    request: Request,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> SearchJobResponse:
    """Create an asynchronous supplier-research job and return immediately."""
    _prune_jobs()
    job_id = uuid4().hex
    job = _SearchJobState(
        jobId=job_id,
        owner=current_user.email,
        status="queued",
        progress=0,
        step="Queued",
        events=[],
        intent=None,
        results=[],
        error=None,
    )
    _append_event(job, "queued", "Queued supplier research job", 0)
    _SEARCH_JOBS[job_id] = job
    structured = req.structured.model_dump() if req.structured else None
    criteria = [criterion.model_dump() for criterion in req.criteria]
    asyncio.create_task(
        _run_search_job(
            job_id,
            req.query,
            request.app.state.agent,
            structured=structured,
            criteria=criteria,
            cache_key=_search_cache_key(current_user.email, req.query, structured, criteria),
        )
    )
    return _public_job(job)


@router.get("/search-jobs/{job_id}", response_model=SearchJobResponse)
async def get_search_job(
    job_id: str,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> SearchJobResponse:
    """Poll a supplier-research job created by POST /search-jobs."""
    job = _SEARCH_JOBS.get(job_id)
    if job is None or job.owner != current_user.email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Search job not found"},
        )
    return _public_job(job)


@router.get("/search-jobs/{job_id}/events")
async def stream_search_job_events(
    job_id: str,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> StreamingResponse:
    """Stream supplier-research job progress as SSE; clients may fall back to polling."""
    job = _SEARCH_JOBS.get(job_id)
    if job is None or job.owner != current_user.email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Search job not found"},
        )
    return StreamingResponse(
        _stream_search_job_events(job_id, current_user.email),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
