# RAGstar Web Server

OOM 로그 진단 시스템 프론트엔드 (Streamlit)

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## 시스템 구조
- 프론트엔드: Streamlit (이 리포)
- 백엔드: FastAPI on AWS EC2 (3.34.90.38:8000)
- AI 워커: 학교 서버 컨테이너 (LangGraph + vLLM + ChromaDB)

## 배포
- Streamlit Cloud: 배포 후 URL 추가 예정
