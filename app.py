import streamlit as st
import requests
import time
import re
import json

# ── Config ──────────────────────────────────────────────
API_BASE = st.secrets.get("API_BASE", "http://3.34.90.38:8000")
POLL_INTERVAL = 3  # seconds

# ── Placeholder examples ────────────────────────────────
SERVER_INFO_EXAMPLE = """Ubuntu 22.04 LTS, Kernel 5.15.0-105-generic, 32GB RAM, swap disabled
Kubernetes worker node v1.29.0, containerd 1.7.13, cgroup v2 unified"""

SERVICE_EXAMPLE = """payments-api v3.1.0 (Go 1.21.5, gRPC + Gin HTTP), single statically-linked binary
Pod limits: memory=2Gi, requests=1Gi (Burstable QoS)
ENV: GOGC=100 (default), GOMEMLIMIT not set, GOMAXPROCS=4
Replicas: 8, ingress traffic balanced via L7"""

RECENT_CHANGES_EXAMPLE = """- Go runtime upgraded from 1.20.12 to 1.21.5 last week (security patches)
- Inbound RPS up ~50% over baseline (new mobile client rollout)
- Added in-memory response cache (sync.Map, no eviction policy) 6 days ago
- Pod memory limit unchanged (still 2Gi)
- pprof shows in-use heap ~1.3GB but RSS reaches 2.0GB before OOM"""

DMESG_EXAMPLE = """[456789.234567] api-server invoked oom-killer: gfp_mask=0xcc0(GFP_KERNEL), order=0, oom_score_adj=994
[456789.234589] CPU: 5 PID: 67890 Comm: api-server Not tainted 5.15.0-105-generic #115-Ubuntu
[456789.234612] Call Trace:
[456789.234623]  <TASK>
[456789.234634]  dump_stack_lvl+0x4a/0x63
[456789.234656]  dump_header+0x4f/0x1f6
[456789.234678]  oom_kill_process.cold+0xb/0x10
[456789.234701]  out_of_memory+0x1cf/0x520
[456789.234723]  mem_cgroup_out_of_memory+0x13a/0x150
[456789.234745]  try_charge_memcg+0x49b/0x540
[456789.234767]  __mem_cgroup_charge+0x29/0x90
[456789.234789]  do_anonymous_page+0x126/0x3d0
[456789.234812]  handle_pte_fault+0x1ab/0x230
[456789.234834]  __handle_mm_fault+0x614/0x6f0
[456789.234856]  handle_mm_fault+0xfd/0x320
[456789.234878]  do_user_addr_fault+0x1aa/0x680
[456789.234901]  exc_page_fault+0x70/0x170
[456789.234923]  asm_exc_page_fault+0x22/0x30
[456789.234945] memory: usage 2097152kB, limit 2097152kB, failcnt 287
[456789.234967] swap: usage 0kB, limit 0kB, failcnt 0
[456789.234989] Memory cgroup stats for /kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod9e8f7c.slice:
[456789.235012]  anon 2086543360
[456789.235023]  file 1572864
[456789.235034]  kernel 6291456
[456789.235045]  kernel_stack 262144
[456789.235056]  pagetables 4194304
[456789.235067]  shmem 0
[456789.235089] memory.events:
[456789.235101]  low 0
[456789.235112]  high 0
[456789.235123]  max 287
[456789.235134]  oom 1
[456789.235145]  oom_kill 1
[456789.235167] Tasks state (memory values in pages):
[456789.235189] [  pid  ]   uid  tgid total_vm      rss pgtables_bytes swapents oom_score_adj name
[456789.235212] [  67890]  1000 67890   821456   519234      4194304        0           994 api-server
[456789.235245] oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0,oom_memcg=/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod9e8f7c.slice,task_memcg=/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod9e8f7c.slice,task=api-server,pid=67890,uid=1000
[456789.235278] Memory cgroup out of memory: Killed process 67890 (api-server) total-vm:3285824kB, anon-rss:2076936kB, file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:4194304 oom_score_adj:994"""

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

/* v2: Model comparison cards */
.model-card {
    background: #FAFBFC; border: 1px solid #DFE6E9; border-radius: 8px;
    padding: 20px 24px;
}
.model-card-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px; padding-bottom: 10px; border-bottom: 1px solid #DFE6E9;
}
.model-card-label { font-size: 15px; font-weight: 700; color: #2D3436; }
.model-card-latency { font-size: 12px; color: #B2BEC3; }
.model-card-row { display: flex; gap: 12px; margin-bottom: 10px; }
.model-card-field {
    flex: 1; background: #F0F3F5; border-radius: 6px; padding: 8px 12px;
}
.model-card-field-label { font-size: 10px; color: #B2BEC3; text-transform: uppercase; margin-bottom: 2px; }
.model-card-field-value { font-size: 14px; font-weight: 600; color: #2D3436; }
.model-card-field-value-green { font-size: 14px; font-weight: 600; color: #00B894; }
.model-card-section { margin-top: 12px; }
.model-card-section-title { font-size: 12px; color: #636E72; font-weight: 600; margin-bottom: 6px; }
.model-card-text { font-size: 13px; color: #2D3436; line-height: 1.7; }
.model-card-empty { text-align: center; padding: 2rem 0; color: #B2BEC3; font-size: 13px; }
.severity-high { background: #D63031; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.severity-medium { background: #E17055; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.severity-low { background: #868E96; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.severity-unknown { background: #B2BEC3; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.action-list { margin: 0; padding: 0; list-style: none; }
.action-list li { font-size: 13px; color: #2D3436; line-height: 1.8; }
.action-list li::before { content: "- "; color: #00B894; font-weight: 600; }

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
    "analyze_submitted": False,
    "_clear_step": 0,
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


# ── v2 helpers ─────────────────────────────────────────
def format_latency(ms):
    if ms is None:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def severity_badge(severity):
    if not severity:
        return ""
    cls = {
        "HIGH": "severity-high",
        "MEDIUM": "severity-medium",
        "LOW": "severity-low",
    }.get(severity.upper(), "severity-unknown")
    return f'<span class="{cls}">{severity}</span>'


def format_gpt_label(model_str):
    if not model_str:
        return "GPT-5.2"
    return model_str.upper() if model_str.lower().startswith("gpt") else model_str


def _highlight_text(text):
    if not text:
        return ""
    esc = str(text).replace("<", "&lt;").replace(">", "&gt;")
    for kw in ["memory.max", "memory.high", "cgroup", "OOM Killer", "oom-killer", "oom_kill"]:
        esc = esc.replace(kw, f'<span class="highlight">{kw}</span>')
    return esc


def render_model_card(label_text, model_data):
    if model_data is None:
        st.markdown(
            f'<div class="model-card">'
            f'<div class="model-card-header"><span class="model-card-label">{label_text}</span></div>'
            f'<div class="model-card-empty">응답 없음</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    oom_type = model_data.get("oom_type", "N/A")
    confidence = model_data.get("confidence")
    conf_str = f"{int(confidence * 100)}%" if isinstance(confidence, (int, float)) else "N/A"
    constraint = (model_data.get("constraint_type") or "").replace("CONSTRAINT_", "") or None
    severity = model_data.get("severity")
    root_cause = model_data.get("root_cause", "")
    evidence = model_data.get("evidence")
    action_guide = model_data.get("action_guide") or {}
    immediate = action_guide.get("immediate", [])
    recommended = action_guide.get("recommended", [])

    sev_html = severity_badge(severity) if severity else ""

    # Top fields row
    fields_html = (
        f'<div class="model-card-row">'
        f'<div class="model-card-field"><div class="model-card-field-label">OOM TYPE</div>'
        f'<div class="model-card-field-value-green">{oom_type}</div></div>'
        f'<div class="model-card-field"><div class="model-card-field-label">CONFIDENCE</div>'
        f'<div class="model-card-field-value">{conf_str}</div></div>'
    )
    if constraint:
        fields_html += (
            f'<div class="model-card-field"><div class="model-card-field-label">CONSTRAINT</div>'
            f'<div class="model-card-field-value">{constraint}</div></div>'
        )
    if severity:
        fields_html += (
            f'<div class="model-card-field"><div class="model-card-field-label">SEVERITY</div>'
            f'<div class="model-card-field-value">{sev_html}</div></div>'
        )
    fields_html += '</div>'

    # Root cause
    rc_html = ""
    if root_cause:
        rc_html = (
            f'<div class="model-card-section">'
            f'<div class="model-card-section-title">// ROOT CAUSE</div>'
            f'<div class="model-card-text">{_highlight_text(root_cause)}</div>'
            f'</div>'
        )

    # Evidence
    ev_html = ""
    if evidence:
        ev_html = (
            f'<div class="model-card-section">'
            f'<div class="model-card-section-title">// EVIDENCE</div>'
            f'<div class="model-card-text">{_highlight_text(evidence)}</div>'
            f'</div>'
        )

    # Action guide: immediate
    imm_html = ""
    if immediate:
        items = "".join(f"<li>{str(a).replace('<', '&lt;').replace('>', '&gt;')}</li>" for a in immediate)
        imm_html = (
            f'<div class="model-card-section">'
            f'<div class="model-card-section-title">// IMMEDIATE</div>'
            f'<ul class="action-list">{items}</ul>'
            f'</div>'
        )

    # Action guide: recommended
    rec_html = ""
    if recommended:
        items = "".join(f"<li>{str(a).replace('<', '&lt;').replace('>', '&gt;')}</li>" for a in recommended)
        rec_html = (
            f'<div class="model-card-section">'
            f'<div class="model-card-section-title">// RECOMMENDED</div>'
            f'<ul class="action-list">{items}</ul>'
            f'</div>'
        )

    st.markdown(
        f'<div class="model-card">'
        f'<div class="model-card-header">'
        f'<span class="model-card-label">{label_text}</span>'
        f'</div>'
        f'{fields_html}{rc_html}{ev_html}{imm_html}{rec_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


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


# CLEAR 2단계: 위젯 생성 전에 session state 비워야 에러 안 남
if st.session_state._clear_step == 2:
    st.session_state._clear_step = 0
    st.session_state.raw_log_input = ""

# ══════════════════════════════════════════════════════════
# INPUT FORM — METADATA + DMESG LOG + BUTTONS
# (모든 위젯을 폼 안에 두어 ANALYZE 한 번에 모든 값이 같이 제출됨)
# ══════════════════════════════════════════════════════════
with st.form("analyze_form", clear_on_submit=False, border=False):
    st.markdown("""
    <div class="section-label">
        <span>// INPUT &mdash; METADATA <span style="color:#B2BEC3">(optional &middot; 검색 품질 향상)</span></span>
        <span class="section-label-right">3 fields</span>
    </div>
    """, unsafe_allow_html=True)

    server_info = st.text_area("[Server Info]", placeholder=SERVER_INFO_EXAMPLE, height=68)
    service = st.text_area("[Service]", placeholder=SERVICE_EXAMPLE, height=110)
    recent_changes = st.text_area("[Recent Changes]", placeholder=RECENT_CHANGES_EXAMPLE, height=130)

    st.markdown("""
    <div class="section-label">
        <span>// INPUT &mdash; DMESG LOG</span>
    </div>
    """, unsafe_allow_html=True)

    raw_log = st.text_area(
        "DMESG Log",
        height=220,
        placeholder=DMESG_EXAMPLE,
        label_visibility="collapsed",
        key="raw_log_input",
    )

    if raw_log and raw_log.strip():
        line_count = len(raw_log.strip().split("\n"))
        byte_size = len(raw_log.encode("utf-8"))
        size_str = f"{byte_size / 1024:.1f} KB" if byte_size >= 1024 else f"{byte_size} B"
        st.caption(f"{line_count} lines · {size_str}")

    col_btn1, col_btn2, col_btn3 = st.columns([2, 1.5, 5])
    with col_btn1:
        analyze_submitted = st.form_submit_button(
            "▶  ANALYZE", type="primary", use_container_width=True
        )
    with col_btn2:
        clear_submitted = st.form_submit_button(
            "CLEAR", use_container_width=True
        )

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
if clear_submitted:
    if st.session_state._clear_step == 0:
        # 1번째: 분석 중지 + 결과 초기화
        st.session_state.diagnosis_id = None
        st.session_state.result = None
        st.session_state.status = None
        st.session_state.error = None
        st.session_state.elapsed = None
        st.session_state.polling = False
        st.session_state.poll_start = None
        st.session_state.analyze_submitted = False
        st.session_state._clear_step = 1
        st.rerun()
    else:
        # 2번째: 다음 rerun에서 위젯 생성 전에 내용 삭제
        st.session_state._clear_step = 2
        st.rerun()


# ══════════════════════════════════════════════════════════
# ANALYZE — POST to backend
# ══════════════════════════════════════════════════════════
if analyze_submitted and st.session_state.diagnosis_id is None:
    st.session_state._clear_step = 0
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

# ── Result (v2: ours/gpt 비교) ─────────────────────────
elif st.session_state.result:
    data = st.session_state.result
    ours_data = data.get("ours")
    gpt_data = data.get("gpt")

    st.markdown("""
    <div class="section-label">
        <span>// DIAGNOSIS RESULT</span>
        <span class="status-badge status-success">SUCCESS</span>
    </div>
    """, unsafe_allow_html=True)

    # 좌/우 비교 카드
    col_left, col_right = st.columns(2)
    with col_left:
        render_model_card("RAGstar", ours_data)
    with col_right:
        gpt_label = format_gpt_label(gpt_data.get("model") if gpt_data else None)
        render_model_card(gpt_label, gpt_data)

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
