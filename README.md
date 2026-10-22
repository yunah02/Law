# 임대차 판례 RAG

주택·상가 임대차 판례와 법령을 찾아 근거와 함께 답하는 RAG 서비스입니다.
SSAFY AI 코어 프로젝트 2회차 과제입니다.

- 사용자가 판례를 고르고 질문하면, 그 판례와 관련 법령 조문·판결문을 근거로 답합니다.
- 답변의 모든 주장에 `[근거 N]` 번호가 붙고, 화면에 인용한 근거만 출처 링크와 함께 보여 줍니다.
- 문서에 없는 내용은 추측하지 않고 "제공된 문서에서 확인되지 않습니다."라고 답합니다.

> 이 서비스는 공개된 법령과 판례를 바탕으로 한 참고용 정보이며 법률 자문이 아닙니다.

## 1. 실행 방법

Python 3.11, Windows Git Bash 기준입니다.

```bash
python -m venv .venv
source .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
export PYTHONUTF8=1                  # Windows에서 한글 requirements 읽기 오류(cp949) 방지
pip install -r requirements.txt
cp .env.example .env                 # .env에 LAW_OC, GMS_KEY를 넣는다
```

`.env`에 넣는 값

| 이름 | 설명 |
| --- | --- |
| `LAW_OC` | 법제처 Open API 인증값 (open.law.go.kr에서 신청) |
| `GMS_KEY` | SSAFY GMS 키 (OpenAI 호환) |

`.env`는 Git에 올리지 않습니다 (`.gitignore`에 포함).

데이터 준비와 실행

```bash
python check_precedents.py --reanalyze   # 판례 목록 수집·분류·선정 → data/items_selected.json (159건)
python indexing.py precedents            # 일상어 요약 생성(캐시) + 판례 색인 (RAG 1)
python indexing.py count                 # 컬렉션별 청크 수 확인
streamlit run app.py                     # 웹 화면
```

터미널에서 확인할 때

```bash
python collect.py "새 집주인이 보증금을 돌려줘야 하나요?" --case 619451   # 수집·선별 결과만
python ask.py "묵시적 갱신 후 해지 통지는 언제 효력이 생기나요?"            # 수집 → 검색 → 답변
python ask.py "..." --top-k 3 --all      # Top-k를 바꾸고 검색된 근거 전체 보기
python ask.py "..." --mmr                # MMR 검색
python ask.py --graph                    # LangGraph 구조 (retrieve → generate)
python compare.py rag                    # RAG 전후 비교 → results/compare_rag.md
python compare.py mmr                    # 유사도·MMR 비교 → results/compare_mmr.md
```

## 2. 기능

### 필수 기능

| 번호 | 기능 | 구현 위치 |
| --- | --- | --- |
| F2001 | 판례 159건 로드 (주택·상가, 쟁점 4개) | `check_precedents.py`, `data_prep/precedents.py` |
| F2002 | 판례 색인 (판례 1건 = 청크 1개, ID = 판례일련번호, 다시 색인해도 늘지 않음) | `indexing.py` |
| F2003 | 프롬프트 1: 질문·판례에서 확인할 조문과 법률 용어 추출 | `collect.py`, `prompts.QUERY_PROMPT` |
| F2004 | 프롬프트 2: 후보 조문 중 질문에 필요한 것만 선별, 판결문 전문은 항상 포함 | `collect.py`, `prompts.SELECT_PROMPT` |
| F2005 | 외부 문서 청킹·색인 (청크 1000자, 중복 200자) | `collect.py`, `config.py` |
| F2006 | 두 컬렉션 검색과 병합·중복 제거 (Top-k 설정) | `retrieval.py` |
| F2007 | LangGraph 2노드 (retrieve → generate), 프롬프트 3 | `graph.py`, `prompts.ANSWER_PROMPT` |
| F2008 | 근거 번호 인용, 인용한 근거만 화면 표시, 인용 없을 때 안내 | `graph.cited_sources`, `app.py` |
| F2009 | 판례별 대화 분리 (판례를 바꾸면 대화가 섞이지 않음) | `app.py` |
| F2010 | 도메인 특화: 요건 점검 (쟁점 요건을 법령·판례 근거로 하나씩 점검, 결과 예측 없음) | `services.check_requirements` |
| F2011 | 조건 기반 판례 찾기 (자연어 조건 → 의미 검색 → LLM 필터) | `services.find_precedents` |
| F2012 | 진행 상태 표시, 색인·키 없음 안내, 법률 자문 아님 안내 | `app.py` |

### 심화 기능

| 번호 | 기능 | 확인 방법 |
| --- | --- | --- |
| F2014 | MMR 검색 (후보 30건 → 다양성 기준 선별). 화면 "검색 설정"에서 켜고 끈다 | `python compare.py mmr` |
| F2015 | RAG 전후 품질 비교 (대표 질문 6개, 3기준) | `python compare.py rag` |
| F2016 | 출처 종류 결합: 판례 메타데이터, 법령 조문, 판결문 전문. 근거마다 종류 표시 | 화면 근거 목록 |

## 3. 데이터 준비와 색인 절차

1. `check_precedents.py`: 법제처 판례 목록에서 임대차 판례를 모아 쟁점(대항력, 우선변제권, 보증금반환, 계약갱신)과 종류(주택, 상가)로 분류하고 159건을 고른다.
2. `indexing.py precedents`: 판례마다 일상어 요약과 한 줄 제목을 만들어 `data/summaries.json`에 저장한다. 판시사항·판결요지·참조조문·요약을 한 텍스트로 묶어 `precedents` 컬렉션에 색인한다.
3. 질문할 때 `collect.py`가 동작한다.
   - 프롬프트 1이 조문과 용어를 뽑는다.
   - 법제처 법령 API로 4개 법령(주택임대차보호법·시행령, 상가건물 임대차보호법·시행령)의 조문을 후보로 만든다.
   - 프롬프트 2가 필요한 조문만 고른다. 선택한 판례의 판결문 전문은 항상 넣는다.
   - 고른 문서를 1000자 단위로 잘라 `legal_docs` 컬렉션에 색인한다.
   - 같은 질문은 `data/collected.json`에 기록해 다시 수집하지 않는다.
4. `graph.py`가 두 컬렉션에서 근거를 찾아 답한다.

API 응답은 `data/raw/`에 캐시한다. 호출 사이에 0.4초 쉬고, 실패하면 3번까지 다시 시도한다.

## 4. 데이터·문서의 출처와 이용 조건

| 데이터 | 출처 | 이용 조건 |
| --- | --- | --- |
| 판례 목록·본문 | 법제처 국가법령정보 공동활용 Open API (`target=prec`) | 판결은 저작권법 제7조에 따라 보호받지 않는 저작물. Open API 이용 신청 후 인증값(OC)으로 호출, 출처 표시 |
| 법령 조문 | 법제처 국가법령정보 공동활용 Open API (`target=law`) | 법령은 저작권법 제7조에 따라 보호받지 않는 저작물. 출처 표시 |
| 일상어 요약 | 판시사항·판결요지를 바탕으로 GMS(gpt-5.4-nano)가 생성 | 원문이 아닌 생성 요약이므로 답변 인용에 쓰지 않음 |

- 근거 링크는 국가법령정보센터 공개 페이지(`law.go.kr/LSW/...`)를 쓴다. 인증값이 들어 있는 API 링크는 화면과 파일에 남기지 않는다.
- 판례는 선고 당시의 법령을 기준으로 한 판단이다. 판례를 인용한 답변에는 이 안내를 자동으로 붙인다.

## 5. 폴더 구조

```
app.py                Streamlit 화면 (질의응답, 판례 찾기, 요건 점검)
config.py             경로, 모델, 청킹·검색 설정
llm.py                GMS 채팅·임베딩 모델
prompts.py            프롬프트 (요약, 1 검색어 추출, 2 선별, 3 답변, 판례 필터, 요건 점검, RAG 없는 답변)
indexing.py           판례 색인 CLI
collect.py            외부 문서 수집·선별·색인
retrieval.py          두 컬렉션 검색, 병합, 중복 제거
graph.py              LangGraph (retrieve → generate)
services.py           판례 찾기, 요건 점검
ask.py                터미널 질의응답
compare.py            RAG 전후 비교, 유사도·MMR 비교
data_prep/
  precedents.py       판례 로드, 요약, 색인 텍스트
  law_api.py          법제처 API 호출, 캐시, 조문 파싱
check_precedents.py   판례 선정 스크립트
```

수집 · 색인 · 검색 · 생성 · 화면을 모듈로 나눴다 (NF2005).
