from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import chromadb
import json
from typing import Any, Optional

import db

app = FastAPI(title="OOM Debugger API")


@app.on_event("startup")
def _startup():
    db.init_db()

# CORS 설정 - React 프론트에서 요청 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 나중에 프론트 주소로 제한
    allow_methods=["*"],
    allow_headers=["*"],
)

# ChromaDB 설정
client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_or_create_collection(
    name="kb_chunks",
    metadata={"description": "OOM knowledge base chunks"}
)


# ==================== 요청/응답 모델 ====================

class SearchRequest(BaseModel):
    question: str
    n_results: int = 5
    category: Optional[str] = None  # oom_killer, swap_exhaustion, cgroup_oom

class AnalyzeRequest(BaseModel):
    log: str           # OOM 로그 원문
    question: str      # 사용자 질문
    n_results: int = 5


# ==================== 엔드포인트 ====================

@app.get("/")
def health_check():
    """서버 상태 확인"""
    return {"status": "ok"}


@app.get("/kb/stats")
def kb_stats():
    """ChromaDB에 저장된 청크 수 확인"""
    count = collection.count()
    return {
        "total_chunks": count,
        "collection_name": "kb_chunks"
    }


@app.post("/kb/upload")
async def upload_chunks(file: UploadFile = File(...)):
    """
    JSONL 파일 업로드 → ChromaDB에 저장
    
    파일 형식 (kb_chunks.jsonl):
    {"chunk_id": "...", "doc_id": "...", "title": "...", "content": "...", "metadata": {...}}
    """
    content = await file.read()
    lines = content.decode("utf-8").strip().split("\n")
    
    added = 0
    skipped = 0
    
    for line in lines:
        chunk = json.loads(line)
        
        # 이미 있는 chunk_id면 스킵
        existing = collection.get(ids=[chunk["chunk_id"]])
        if existing["ids"]:
            skipped += 1
            continue
        
        # ChromaDB에 저장
        collection.add(
            ids=[chunk["chunk_id"]],
            documents=[chunk["content"]],
            metadatas=[{
                "doc_id": chunk["doc_id"],
                "chunk_index": chunk.get("chunk_index", 0),
                "title": chunk["title"],
                "error_category": chunk.get("metadata", {}).get("error_category", ""),
                "keywords": ",".join(chunk.get("metadata", {}).get("keywords", []))
            }]
        )
        added += 1
    
    return {
        "message": f"업로드 완료: {added}개 추가, {skipped}개 스킵 (이미 존재)",
        "total_chunks": collection.count()
    }


@app.post("/search")
def search(req: SearchRequest):
    """질문으로 관련 문서 검색"""
    
    # 카테고리 필터 적용
    where_filter = None
    if req.category:
        where_filter = {"error_category": req.category}
    
    results = collection.query(
        query_texts=[req.question],
        n_results=req.n_results,
        where=where_filter
    )
    
    # 검색 결과 정리
    chunks = []
    for i in range(len(results["ids"][0])):
        chunks.append({
            "chunk_id": results["ids"][0][i],
            "content": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i] if results.get("distances") else None
        })
    
    return {
        "question": req.question,
        "results": chunks
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    """
    OOM 로그 분석 (메인 엔드포인트)
    
    현재: ChromaDB 검색 결과만 반환
    TODO: LangChain + Ollama 연동 후 AI 분석 답변 추가
    """
    
    # 로그 + 질문을 합쳐서 검색 쿼리로 사용
    search_query = f"{req.question} {req.log[:500]}"
    
    results = collection.query(
        query_texts=[search_query],
        n_results=req.n_results
    )
    
    # 검색된 문서 정리
    references = []
    for i in range(len(results["ids"][0])):
        references.append({
            "chunk_id": results["ids"][0][i],
            "title": results["metadatas"][0][i].get("title", ""),
            "content": results["documents"][0][i],
            "distance": results["distances"][0][i] if results.get("distances") else None
        })
    
    return {
        "log": req.log,
        "question": req.question,
        # TODO: LLM 연동 후 이 부분에 AI 분석 결과 추가
        "answer": "[LLM 미연동] 아래 관련 문서를 참고하세요.",
        "references": references
    }


# ==================== Diagnosis API (frontend + worker) ====================

class DiagnosisSubmit(BaseModel):
    raw_log: str
    metadata: Optional[dict] = None
    source: Optional[str] = None


class StatusUpdate(BaseModel):
    status: str
    error: Optional[str] = None


class ResultSubmit(BaseModel):
    result: dict
    intermediate_results: Optional[dict] = None


ALLOWED_STATUSES = {"pending", "running", "success", "failed"}


def _public_view(row: dict) -> dict:
    """Frontend-facing payload — flattens result fields under 'result'."""
    status = row["status"]
    payload: dict[str, Any] = {
        "diagnosis_id": row["diagnosis_id"],
        "status": status,
    }
    if status == "success":
        payload["result"] = {
            "oom_type": row.get("oom_type"),
            "constraint_type": row.get("constraint_type"),
            "confidence": row.get("confidence"),
            "root_cause": row.get("root_cause"),
            "action_guide": row.get("action_guide") or [],
        }
        if row.get("intermediate_results"):
            payload["intermediate_results"] = row["intermediate_results"]
    elif status == "failed":
        payload["error"] = row.get("error_message") or "분석에 실패했습니다."
    return payload


@app.post("/api/v1/diagnosis")
def submit_diagnosis(req: DiagnosisSubmit):
    """프론트엔드: OOM 로그 제출 → diagnosis_id 발급, pending 큐에 등록."""
    if not req.raw_log or not req.raw_log.strip():
        raise HTTPException(status_code=400, detail="raw_log is empty")
    diagnosis_id = db.create_diagnosis(req.raw_log, req.metadata, req.source)
    return {"diagnosis_id": diagnosis_id, "status": "pending"}


@app.get("/api/v1/diagnosis/pending")
def fetch_pending_task():
    """Worker: 가장 오래된 pending 작업을 원자적으로 가져와 running으로 전환."""
    row = db.claim_next_pending()
    if not row:
        raise HTTPException(status_code=404, detail="no pending task")
    return {
        "diagnosis_id": row["diagnosis_id"],
        "raw_log": row["raw_log"],
        "metadata": row.get("metadata") or {},
    }


@app.get("/api/v1/diagnosis/{diagnosis_id}")
def get_diagnosis(diagnosis_id: int):
    """프론트엔드: 폴링용. pending/running/success/failed 상태와 결과 반환."""
    row = db.get_diagnosis(diagnosis_id)
    if not row:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    return _public_view(row)


@app.patch("/api/v1/diagnosis/{diagnosis_id}/status")
def patch_status(diagnosis_id: int, body: StatusUpdate):
    """Worker: running/failed 등 상태 업데이트."""
    if body.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {body.status}")
    ok = db.update_status(diagnosis_id, body.status, body.error)
    if not ok:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    return {"ok": True}


@app.post("/api/v1/diagnosis/{diagnosis_id}/result")
def submit_result(diagnosis_id: int, body: ResultSubmit):
    """Worker: 최종 진단 결과 저장 (status를 success로 전환)."""
    ok = db.save_result(diagnosis_id, body.result, body.intermediate_results)
    if not ok:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    return {"ok": True}


# ==================== 서버 실행 ====================
# uvicorn main:app --reload --host 0.0.0.0 --port 8000