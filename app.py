import streamlit as st
import requests
import time
import re
import json

# ── Config ──────────────────────────────────────────────
API_BASE = st.secrets.get("API_BASE", "http://3.34.90.38:8000")
POLL_INTERVAL = 3  # seconds

LOADING_TIPS = [
    "OOM의 70% 이상은 Swap 미설정 + 단일 프로세스 과점유 조합에서 발생합니다. free -m으로 SwapTotal부터 확인해보세요.",
    "cgroup_oom은 컨테이너의 memory.limit 초과 시 발생합니다. /sys/fs/cgroup/memory.max 또는 docker stats로 한도를 점검해보세요.",
    "constraint=CONSTRAINT_NONE은 시스템 전체 OOM(global_oom)을 의미합니다. constraint=MEMCG는 cgroup 한도 초과입니다.",
    "page_alloc_failure는 free 메모리가 충분해도 연속된 페이지가 없을 때 발생합니다. /proc/buddyinfo로 단편화를 확인하세요.",
    "oom_score_adj=-1000으로 설정된 프로세스는 OOM Killer가 절대 죽이지 않습니다. 핵심 서비스(DB 등)에 적용하세요.",
    "swap_exhaustion은 Swap이 100% 차서 회수 불가일 때 발생합니다. vm.swappiness 조정과 Swap 영역 확장을 고려하세요.",
    "OOM Killer는 oom_score가 가장 높은 프로세스를 선택합니다. 점수는 RSS + oom_score_adj 보정으로 계산됩니다.",
    "vm.overcommit_memory=2 설정 시 커밋 한도를 초과하는 할당을 거부합니다. OOM 자체를 예방하는 강력한 방법입니다.",
    "JVM 기반 서비스가 죽었다면 -Xmx 힙 설정과 컨테이너 메모리 한도를 비교해보세요. 보통 -Xmx가 한도의 75%를 넘으면 위험합니다.",
    "free(N) < min(M) 조건이 충족되면 즉시 OOM이 발동됩니다. min은 vm.min_free_kbytes로 조정 가능합니다.",
    "메모리 압박이 점진적으로 진행됐다면 dmesg 외에 sar -r 출력을 함께 보면 시간대별 추이가 보입니다.",
    "OOM이 반복된다면 일회성 조치(reboot) 대신 메모리 한도, oom_score_adj, Swap, overcommit 정책 4가지를 함께 점검하세요.",
]

st.set_page_config(page_title="RAGstar", page_icon="R", layout="wide")

# ── Custom CSS ──────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

.block-container, .block-container * {
    font-family: 'JetBrains Mono', 'SF Mono', Monaco, Consolas, monospace !important;
}

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

.block-container { max-width: 900px; padding-top: 2rem; }

.header-row {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 0.5rem;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.header-icon {
    background: #00B894; color: white; width: 40px; height: 40px;
    border-radius: 10px; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 18px;
}
.header-title { font-size: 22px; font-weight: 700; color: #2D3436; }
.header-version {
    font-size: 12px; color: #636E72; border: 1px solid #DFE6E9;
    border-radius: 4px; padding: 2px 8px; margin-left: 4px;
}
.api-status { font-size: 13px; color: #636E72; }
.api-dot-green { color: #00B894; }
.api-dot-red { color: #D63031; }

.section-label {
    font-size: 13px; color: #636E72; margin-bottom: 6px; margin-top: 1.5rem;
    display: flex; justify-content: space-between; align-items: center;
}
.section-label-right { font-size: 12px; color: #B2BEC3; }

.log-area {
    background: #FAFBFC; border: 1px solid #DFE6E9; border-radius: 8px;
    padding: 16px 20px; font-size: 13px; line-height: 1.8;
    color: #2D3436; overflow-x: auto;
}
.log-area .line-num { color: #B2BEC3; margin-right: 16px; user-select: none; display: inline-block; width: 24px; text-align: right; }
.log-area .kw-green { color: #00B894; }
.log-area .kw-orange { color: #E17055; }
.log-area .kw-purple { color: #6C5CE7; }

.result-card {
    background: #FAFBFC; border: 1px solid #DFE6E9; border-radius: 8px;
    padding: 16px 20px; height: 80px;
}
.result-card .card-label { font-size: 11px; color: #B2BEC3; text-transform: uppercase; margin-bottom: 4px; }
.result-card .card-value { font-size: 16px; font-weight: 600; color: #00B894; }
.result-card .card-value-dark { font-size: 16px; font-weight: 600; color: #2D3436; }
.confidence-sub { font-size: 14px; color: #B2BEC3; font-weight: 400; }

.cause-box {
    background: #FAFBFC; border: 1px solid #DFE6E9; border-radius: 8px;
    padding: 20px 24px; margin-top: 12px;
}
.cause-box .box-title { color: #00B894; font-weight: 600; font-size: 14px; margin-bottom: 12px; }
.cause-box .box-content { font-size: 14px; color: #2D3436; line-height: 1.8; }
.cause-box .highlight { background: #E8F8F5; color: #00B894; padding: 1px 6px; border-radius: 3px; font-weight: 500; }

.action-item { margin-bottom: 8px; display: flex; align-items: flex-start; gap: 12px; }
.action-num { color: #B2BEC3; font-weight: 600; min-width: 24px; }

.status-badge {
    display: inline-block; padding: 3px 12px; border-radius: 4px;
    font-size: 12px; font-weight: 600; text-transform: uppercase;
}
.status-success { background: #E8F8F5; color: #00B894; border: 1px solid #00B894; }
.status-failed { background: #FFEAEA; color: #D63031; border: 1px solid #D63031; }

.elapsed { font-size: 13px; color: #B2BEC3; text-align: right; }
.footer-info { font-size: 12px; color: #B2BEC3; margin-top: 2rem; display: flex; justify-content: space-between; }
.dashed-divider { border-top: 2px dashed #DFE6E9; margin: 1.5rem 0; }

/* Button overrides */
.stButton button {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 13px !important;
    border-radius: 6px !important;
    padding: 8px 24px !important;
    min-height: 40px !important;
    white-space: nowrap !important;
}

textarea { font-family: 'JetBrains Mono', monospace !important; font-size: 13px !important; }
input { font-family: 'JetBrains Mono', monospace !important; font-size: 13px !important; }
</style>
""", unsafe_allow_html=True)


# ── Session state ───────────────────────────────────────
for key, val in {
    "diagnosis_id": None,
    "result": None,
    "status": None,
    "error": None,
    "elapsed": None,
    "polling": False,
    "poll_start": None,
    "analyze_requested": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ── API health ──────────────────────────────────────────
@st.cache_data(ttl=30)
def check_api():
    try:
        r = requests.get(f"{API_BASE}/docs", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ══════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════
api_ok = check_api()
dot_class = "api-dot-green" if api_ok else "api-dot-red"
dot_text = "API connected" if api_ok else "API disconnected"

st.markdown(f"""
<div class="header-row">
    <div class="header-left">
        <div class="header-icon">R</div>
        <span class="header-title">RAGstar</span>
        <span class="header-version">v0.1.0</span>
    </div>
    <div class="api-status"><span class="{dot_class}">&bull;</span> {dot_text}</div>
</div>
""", unsafe_allow_html=True)

st.markdown("## OOM Log Diagnostic")
st.markdown(
    '<span style="color:#636E72;font-size:14px;">'
    "dmesg 로그와 시스템 메타데이터를 함께 입력하면 RAG 파이프라인이 원인과 조치 가이드를 생성합니다."
    "</span>",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════
# INPUT — METADATA
# ══════════════════════════════════════════════════════════
st.markdown("""
<div class="section-label">
    <span>// INPUT &mdash; METADATA <span style="color:#B2BEC3">(optional &middot; 검색 품질 향상)</span></span>
    <span class="section-label-right">3 fields</span>
</div>
""", unsafe_allow_html=True)

server_info = st.text_input("[Server Info]", placeholder="예: Ubuntu 22.04, Kernel 5.15, 32GB RAM")
service = st.text_input("[Service]", placeholder="예: payment-api v2.3.1 (deployed 2 days ago)")
recent_changes = st.text_input("[Recent Changes]", placeholder="예: Increased JVM heap from 4G to 8G")

has_meta = any([server_info, service, recent_changes])


# ══════════════════════════════════════════════════════════
# INPUT — DMESG LOG
# ══════════════════════════════════════════════════════════
st.markdown("""
<div class="section-label">
    <span>// INPUT &mdash; DMESG LOG</span>
</div>
""", unsafe_allow_html=True)

# ★ widget 먼저 생성, 그 다음에 값 사용
raw_log = st.text_area(
    "DMESG Log",
    height=220,
    placeholder="여기에 dmesg 로그를 붙여넣으세요...",
    label_visibility="collapsed",
)

# Stats (widget 생성 이후에 값 읽기)
if raw_log and raw_log.strip():
    line_count = len(raw_log.strip().split("\n"))
    byte_size = len(raw_log.encode("utf-8"))
    size_str = f"{byte_size / 1024:.1f} KB" if byte_size >= 1024 else f"{byte_size} B"
    st.caption(f"{line_count} lines · {size_str}")


# ══════════════════════════════════════════════════════════
# BUTTONS
# ══════════════════════════════════════════════════════════
def _on_analyze_click():
    st.session_state.analyze_requested = True

col_btn1, col_btn2, col_btn3 = st.columns([2, 1.5, 5])
with col_btn1:
    st.button("▶  ANALYZE", type="primary", use_container_width=True, on_click=_on_analyze_click)
with col_btn2:
    clear_clicked = st.button("CLEAR", type="secondary", use_container_width=True)

if st.session_state.elapsed is not None:
    with col_btn3:
        st.markdown(
            f'<div class="elapsed" style="padding-top:8px;">'
            f'elapsed: <span style="color:#00B894">{st.session_state.elapsed:.2f}s</span>'
            f"</div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════
# CLEAR
# ══════════════════════════════════════════════════════════
if clear_clicked:
    st.session_state.diagnosis_id = None
    st.session_state.result = None
    st.session_state.status = None
    st.session_state.error = None
    st.session_state.elapsed = None
    st.session_state.polling = False
    st.session_state.poll_start = None
    st.session_state.analyze_requested = False
    st.rerun()


# ══════════════════════════════════════════════════════════
# ANALYZE — POST to backend
# ══════════════════════════════════════════════════════════
if st.session_state.analyze_requested and st.session_state.diagnosis_id is None:
    st.session_state.analyze_requested = False
    # 1. 입력값 검증
    if not raw_log or not raw_log.strip():
        st.error("DMESG 로그를 입력해주세요.")
        st.stop()

    # 2. 이전 상태 초기화
    st.session_state.result = None
    st.session_state.error = None
    st.session_state.elapsed = None
    st.session_state.poll_start = time.time()

    # 3. 메타데이터 조립
    metadata = {}
    if server_info:
        metadata["server_info"] = server_info
    if service:
        metadata["service"] = service
    if recent_changes:
        metadata["recent_changes"] = recent_changes

    payload = {
        "raw_log": raw_log.strip(),
        "metadata": metadata if metadata else None,
        "source": "paste",
    }

    # 4. 백엔드 호출
    try:
        resp = requests.post(f"{API_BASE}/api/v1/diagnosis", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        diag_id = data.get("diagnosis_id") or data.get("id") or data.get("diagnosisId")
        if not diag_id:
            st.session_state.error = (
                f"백엔드 응답에 diagnosis_id 없음: {json.dumps(data, ensure_ascii=False)}"
            )
        else:
            st.session_state.diagnosis_id = diag_id
            st.session_state.status = data.get("status", "pending")
            st.session_state.polling = True

    except requests.exceptions.ConnectionError:
        st.session_state.error = (
            f"백엔드 서버에 연결할 수 없습니다 ({API_BASE})"
        )
    except requests.exceptions.Timeout:
        st.session_state.error = "백엔드 응답 시간 초과 (10s timeout)"
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:300] if e.response else "no body"
        st.session_state.error = f"HTTP {e.response.status_code}: {body}"
    except Exception as e:
        st.session_state.error = f"요청 실패: {type(e).__name__}: {e}"

    st.rerun()


# ══════════════════════════════════════════════════════════
# OUTPUT AREA  (divider 아래: 에러 / 스피너 / 결과 중 하나만)
# ══════════════════════════════════════════════════════════
st.markdown('<div class="dashed-divider"></div>', unsafe_allow_html=True)

# ── Error ───────────────────────────────────────────────
if st.session_state.error:
    st.error(st.session_state.error)
    if st.session_state.status == "failed":
        if st.button("RETRY", type="primary"):
            st.session_state.diagnosis_id = None
            st.session_state.result = None
            st.session_state.status = None
            st.session_state.error = None
            st.session_state.elapsed = None
            st.session_state.polling = False
            st.session_state.poll_start = None
            st.rerun()

# ── Polling: show spinner, then poll ────────────────────
elif st.session_state.polling and st.session_state.diagnosis_id:
    elapsed_so_far = (
        time.time() - st.session_state.poll_start if st.session_state.poll_start else 0
    )

    # Timeout: 1200초(20분) 초과 시 중단
    if elapsed_so_far > 1200:
        st.session_state.polling = False
        st.session_state.error = (
            "20분 타임아웃: AI 서버 점검 중일 수 있습니다. "
            "잠시 후 다시 시도해주세요."
        )
        st.session_state.status = "failed"
        st.rerun()

    # 인디케이터 렌더 (sleep 전에 표시되어 사용자에게 보임)
    tip_index = max(0, int(elapsed_so_far // 5)) % len(LOADING_TIPS)
    tip_text = LOADING_TIPS[tip_index]

    st.markdown(
        f'<div style="text-align:center;padding:2rem 0;">'
        f'<div style="font-size:16px;color:#636E72;">&#9203; 분석 중...</div>'
        f'<div style="font-size:13px;color:#B2BEC3;margin-top:8px;">'
        f"diagnosis_id: {st.session_state.diagnosis_id} · {elapsed_so_far:.1f}s"
        f"</div>"
        f'<div style="margin-top:20px;text-align:left;max-width:520px;margin-left:auto;margin-right:auto;">'
        f'<div style="font-size:12px;color:#636E72;margin-bottom:6px;">// TIP</div>'
        f'<div style="font-size:12px;color:#636E72;line-height:1.6;'
        f'font-family:\'JetBrains Mono\',monospace;">💡 {tip_text}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Poll backend
    diag_id = st.session_state.diagnosis_id
    try:
        resp = requests.get(f"{API_BASE}/api/v1/diagnosis/{diag_id}", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "pending")
        st.session_state.status = status

        if status == "success":
            st.session_state.result = data
            st.session_state.polling = False
            if st.session_state.poll_start:
                st.session_state.elapsed = time.time() - st.session_state.poll_start
            st.rerun()
        elif status == "failed":
            st.session_state.error = data.get("error", "분석에 실패했습니다.")
            st.session_state.polling = False
            if st.session_state.poll_start:
                st.session_state.elapsed = time.time() - st.session_state.poll_start
            st.rerun()
        else:
            # pending / running → 2초 대기 후 재실행
            time.sleep(2)
            st.rerun()

    except Exception as e:
        st.session_state.error = f"Polling 오류: {type(e).__name__}: {e}"
        st.session_state.polling = False
        st.rerun()

# ── Result ──────────────────────────────────────────────
elif st.session_state.result:
    data = st.session_state.result
    result_data = data.get("result", data)

    st.markdown("""
    <div class="section-label">
        <span>// DIAGNOSIS RESULT</span>
        <span class="status-badge status-success">SUCCESS</span>
    </div>
    """, unsafe_allow_html=True)

    # Cards
    oom_type = result_data.get("oom_type", "N/A")
    constraint = result_data.get("constraint_type", result_data.get("constraint", "N/A"))
    confidence = result_data.get("confidence", "N/A")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f'<div class="result-card"><div class="card-label">OOM_TYPE</div><div class="card-value">{oom_type}</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="result-card"><div class="card-label">CONSTRAINT</div><div class="card-value-dark">{constraint}</div></div>', unsafe_allow_html=True)
    with c3:
        conf_str = f'{confidence:.2f} <span class="confidence-sub">/ 1.00</span>' if isinstance(confidence, (int, float)) else str(confidence)
        st.markdown(f'<div class="result-card"><div class="card-label">CONFIDENCE</div><div class="card-value-dark">{conf_str}</div></div>', unsafe_allow_html=True)

    # Root Cause
    root_cause = result_data.get("root_cause", "")
    if root_cause:
        dc = root_cause.replace("<", "&lt;").replace(">", "&gt;")
        for t in ["memory.max", "memory.high", "cgroup", "OOM Killer", "oom-killer", "oom_kill"]:
            dc = dc.replace(t, f'<span class="highlight">{t}</span>')
        st.markdown(f'<div class="cause-box"><div class="box-title">\u25B6 ROOT CAUSE</div><div class="box-content">{dc}</div></div>', unsafe_allow_html=True)

    # Action Guide
    actions = result_data.get("action_guide", result_data.get("actions", []))
    if actions:
        ah = ""
        for i, a in enumerate(actions, 1):
            at = str(a).replace("<", "&lt;").replace(">", "&gt;")
            at = re.sub(r"(--\w+)", r'<span class="highlight">\1</span>', at)
            ah += f'<div class="action-item"><span class="action-num">{i:02d}</span><span>{at}</span></div>'
        st.markdown(f'<div class="cause-box"><div class="box-title">\u25B6 ACTION GUIDE</div><div class="box-content">{ah}</div></div>', unsafe_allow_html=True)

    # Footer
    chunks = result_data.get("retrieved_chunks", result_data.get("chunks_count", ""))
    top_score = result_data.get("top_score", "")
    meta_filter = "on" if has_meta else "off"
    fp = []
    if chunks:
        fp.append(f"retrieved <b>{chunks}</b> chunks from KB")
    if top_score:
        fp.append(f"top score: <b>{top_score}</b>")
    fp.append(f'metadata filter: <span style="color:#00B894">{meta_filter}</span>')
    st.markdown(f'<div class="footer-info"><span>// {" · ".join(fp)}</span></div>', unsafe_allow_html=True)

# ── Idle ────────────────────────────────────────────────
else:
    if raw_log and raw_log.strip() and st.session_state.diagnosis_id is None:
        # Show highlighted preview (only before analysis)
        lines = raw_log.strip().split("\n")
        lh = ""
        for i, line in enumerate(lines[:20], 1):
            esc = line.replace("<", "&lt;").replace(">", "&gt;")
            for kw in ["oom-killer", "oom_kill_process", "out of memory", "Killed process"]:
                esc = esc.replace(kw, f'<span class="kw-orange">{kw}</span>')
            for kw in ["memory.max", "Memory cgroup", "memory cgroup", "cgroup"]:
                esc = esc.replace(kw, f'<span class="kw-green">{kw}</span>')
            esc = re.sub(r"(\d+[GMK]B?\b)", r'<span class="kw-purple">\1</span>', esc)
            lh += f'<div><span class="line-num">{i}</span>{esc}</div>'
        if len(lines) > 20:
            lh += f'<div style="color:#B2BEC3;text-align:center;margin-top:4px;">... +{len(lines) - 20} more lines</div>'
        st.markdown(f'<div class="log-area">{lh}</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="text-align:center;padding:2rem 0;color:#B2BEC3;font-size:13px;">'
            "// 로그를 입력하고 ANALYZE를 클릭하세요"
            "</div>",
            unsafe_allow_html=True,
        )
