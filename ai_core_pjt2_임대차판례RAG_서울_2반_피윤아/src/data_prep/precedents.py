"""판례 항목 준비 (F2001). 도메인에 묶인 부분은 data_prep에 모은다 (NF2005).

- load_items()       data/items_selected.json 로드
- make_summaries()   판례마다 일상어 요약·한 줄 제목 생성 (data/summaries.json에 캐시)
- to_document()      판례 1건 → 색인 텍스트 1개 + 메타데이터
"""

import json
import re

from langchain_core.documents import Document

import config
from prompts import SUMMARY_PROMPT


def load_items() -> list[dict]:
    if not config.ITEMS_JSON.exists():
        raise FileNotFoundError(
            f"{config.ITEMS_JSON.name}이 없습니다. 먼저 python check_precedents.py --reanalyze 를 실행하세요."
        )
    return json.loads(config.ITEMS_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 일상어 요약
def parse_json_object(text: str) -> dict:
    """LLM 응답에서 JSON 객체 하나를 꺼낸다. ```json 코드 블록으로 감싸도 읽는다."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("JSON 객체가 없습니다.")
    return json.loads(match.group(0))


def load_summaries() -> dict[str, dict]:
    if config.SUMMARIES_JSON.exists():
        return json.loads(config.SUMMARIES_JSON.read_text(encoding="utf-8"))
    return {}


def make_summaries(items: list[dict], model) -> dict[str, dict]:
    """요약이 없는 판례만 LLM으로 만든다. 이미 만든 것은 다시 만들지 않는다."""
    summaries = load_summaries()
    todo = [it for it in items if it["판례일련번호"] not in summaries]
    print(f"일상어 요약: 저장된 {len(summaries)}건, 새로 만들 {len(todo)}건")
    for n, item in enumerate(todo, start=1):
        prompt = SUMMARY_PROMPT.format(
            case_name=item["사건명"], holding=item["판시사항"], gist=item["판결요지"],
        )
        try:
            data = parse_json_object(model.invoke(prompt).content)
            summaries[item["판례일련번호"]] = {
                "plain": str(data.get("plain", "")).strip(),
                "title": str(data.get("title", "")).strip(),
            }
        except Exception as exc:  # 한 건이 실패해도 나머지는 계속한다. 다시 실행하면 실패한 건만 재시도
            print(f"  - {item['판례일련번호']} 실패({type(exc).__name__})")
            continue
        if n % 10 == 0 or n == len(todo):
            config.SUMMARIES_JSON.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  {n}/{len(todo)}", flush=True)
    return summaries


# ---------------------------------------------------------------- 색인 텍스트
def year_of(item: dict) -> str:
    digits = re.sub(r"\D", "", item.get("선고일자", ""))
    return digits[:4]


def case_title(item: dict) -> str:
    """사건명에서 [ ] 안의 설명을 뺀 짧은 이름. 예) 건물인도"""
    return re.sub(r"\[.*\]", "", item["사건명"]).strip() or item["사건명"]


def display_name(item: dict, summary: dict | None) -> str:
    """화면 표시명: [쟁점] 한 줄 요약 · 법원 사건번호 (선고연도) (F2009)"""
    title = (summary or {}).get("title") or case_title(item)
    return f"[{item['대표쟁점']}] {title} · {item['법원명']} {item['사건번호']} ({year_of(item)})"


def source_url(case_id: str) -> str:
    """인증키가 없는 판례 원문 주소 (NF2004). 판례상세링크는 인증키가 들어 있어 쓰지 않는다."""
    return f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}"


def index_text(item: dict, summary: dict | None) -> str:
    """판례 1건을 검색 텍스트 1개로 만든다. 정의서 4장의 템플릿을 따른다.

    상황 서술("집주인이 나가래요")로도 찾을 수 있게 일상어 요약을 넣는다.
    길면 판결요지 뒤쪽을 줄여 MAX_INDEX_CHARS에 맞춘다. 참조조문은 항상 남긴다.
    """
    head = (
        f"[{item['사건명']}]\n"
        f"사건번호: {item['사건번호']} | 법원: {item['법원명']} | 선고일: {item['선고일자']}"
        f" | 구분: {item['구분']} | 쟁점: {', '.join(item['쟁점'])}\n"
    )
    if summary and summary.get("plain"):
        head += f"일상어 요약: {summary['plain']}\n"
    tail = f"\n참조조문: {item['참조조문']}"
    body = f"판시사항: {item['판시사항']}\n판결요지: {item['판결요지']}"
    budget = config.MAX_INDEX_CHARS - len(head) - len(tail)
    if len(body) > budget:
        body = body[: max(budget - 3, 0)] + "..."
    return head + body + tail


def to_document(item: dict, summary: dict | None) -> Document:
    """Chroma 메타데이터에는 문자열·숫자·불리언만 넣을 수 있어 목록은 '/'로 잇는다."""
    case_id = item["판례일련번호"]
    return Document(
        id=case_id,
        page_content=index_text(item, summary),
        metadata={
            "source_type": "판례",
            "case_id": case_id,
            "case_no": item["사건번호"],
            "case_name": item["사건명"],
            "court": item["법원명"],
            "date": item["선고일자"],
            "year": int(year_of(item) or 0),
            "kind": item["구분"],
            "issues": "/".join(item["쟁점"]),
            "main_issue": item["대표쟁점"],
            "display_name": display_name(item, summary),
            "refs": item["참조조문"],
            "plain_generated": bool(summary and summary.get("plain")),
            "title": f"{item['법원명']} {item['사건번호']} 판결",
            "source_url": source_url(case_id),
        },
    )
