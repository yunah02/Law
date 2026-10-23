"""실행 설정. 청킹·검색 값은 여기서만 바꾼다 (F2005, F2006, F2014).

값을 바꾼 뒤 다시 색인하거나 다시 질문해서 근거가 어떻게 달라지는지 비교한다 (검증 4.2, 4.3).
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent      # src 폴더 (소스 코드)
ROOT_DIR = BASE_DIR.parent                       # 제출 폴더 (data, results, README)

# ---------------------------------------------------------------- 경로
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"                       # API 응답 캐시 (Git 제외)
ITEMS_JSON = DATA_DIR / "items_selected.json"    # check_precedents.py 결과 (판례 159건)
SUMMARIES_JSON = DATA_DIR / "summaries.json"     # 일상어 요약·한 줄 제목 (한 번 생성해 저장)
CHROMA_DIR = ROOT_DIR / "chroma_db"              # Vector DB (Git 제외)
COLLECTED_JSON = DATA_DIR / "collected.json"     # 이미 수집한 검색어 기록 (NF2002)

# ---------------------------------------------------------------- LLM (SSAFY GMS)
GMS_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/"
CHAT_MODEL = "gpt-5.4-nano"
EMBED_MODEL = "text-embedding-3-small"

# ---------------------------------------------------------------- 컬렉션
PRECEDENT_COLLECTION = "precedents"   # RAG 1: 판례 1건 = 청크 1개
LEGAL_DOC_COLLECTION = "legal_docs"   # RAG 2: 법령 조문, 판결문 전문, 해석례

# ---------------------------------------------------------------- 외부 문서 수집 (법제처 Open API)
LAW_LIST_URL = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"
API_SLEEP = 0.4          # 호출 간격(초). NF2006
API_RETRIES = 3
USE_EXPC = False         # 법령해석례 사용 여부. 신청·호출을 확인한 뒤 True로 바꾼다
MAX_CANDIDATES = 20      # 선별 프롬프트에 보여 줄 후보 문서 수

# ---------------------------------------------------------------- 색인
MAX_INDEX_CHARS = 3000   # 판례 색인 텍스트 최대 길이. 넘으면 판결요지 뒤쪽을 줄인다
CHUNK_SIZE = 1000        # 외부 문서 청크 크기 (자)
CHUNK_OVERLAP = 200      # 외부 문서 청크 중복 길이 (자)

# ---------------------------------------------------------------- 검색
TOP_K = 6                # 외부 문서에서 가져올 근거 수
SEARCH_TYPE = "similarity"   # "similarity" 또는 "mmr" (F2014)
MMR_FETCH_K = 30         # MMR일 때 먼저 넉넉히 가져올 후보 수
GENERAL_PRECEDENT_K = 2  # 판례를 고르지 않은 일반 질문에서 함께 볼 판례 수
HISTORY_TURNS = 4        # 답변 생성에 넘길 이전 대화 수 (질문·답변 합쳐서)
