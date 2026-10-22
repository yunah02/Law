"""법령 본문 API 호출 확인 (현행법령)

준비:
    .env 파일에  LAW_OC=발급받은_인증키   (check_precedents.py와 같은 키)
    법제처 Open API 신청 목록에 '법령' 항목도 체크되어 있어야 한다.

실행 (law-main 폴더에서):
    python check_law.py

하는 일
    1. lawSearch.do (target=law) 로 법령 이름을 검색해 법령일련번호(MST)를 찾는다.
    2. lawService.do (target=law, MST=...) 로 법령 본문(JSON)을 받는다.
    3. 조문 앞 5개를 출력해 호출이 되는지 확인한다.

결과:
    data/raw/law_*.json   API 응답 캐시 (Git 제외)
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OC = os.environ.get("LAW_OC")
SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
HEADERS = {"User-Agent": "law-rag-student-project/1.0"}
RAW = Path("data/raw")
SLEEP = 0.4  # 호출 간격(초). NF2006
RETRIES = 3

LAWS = [
    "주택임대차보호법",
    "주택임대차보호법 시행령",
    "상가건물 임대차보호법",
    "상가건물 임대차보호법 시행령",
]


def get_json(url: str, params: dict, cache: Path) -> dict:
    """캐시가 있으면 재호출하지 않는다 (NF2002). 실패 시 재시도한다 (NF2006)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    last = None
    for attempt in range(RETRIES):
        try:
            res = requests.get(url, params=params, timeout=20, headers=HEADERS)
            res.raise_for_status()
            data = res.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last = type(exc).__name__  # 오류 문장에는 인증키가 든 URL이 있을 수 있어 이름만 남긴다
            time.sleep(SLEEP * 2 ** (attempt + 1))
    else:
        raise RuntimeError(f"호출 실패({last})")
    RAW.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(SLEEP)
    return data


def find_law(name: str) -> dict | None:
    """법령 이름으로 검색해 이름이 정확히 같은 현행법령 1건을 돌려준다."""
    params = {"OC": OC, "target": "law", "type": "JSON", "query": name}
    data = get_json(SEARCH_URL, params, RAW / f"law_search_{name}.json")
    items = data.get("LawSearch", {}).get("law", [])
    if isinstance(items, dict):
        items = [items]
    for item in items:
        if item.get("법령명한글") == name:
            return item
    print(f"  [{name}] 이름이 같은 법령이 없습니다. 검색 결과 {len(items)}건:",
          [i.get("법령명한글") for i in items[:5]])
    return None


def fetch_law(mst: str) -> dict:
    params = {"OC": OC, "target": "law", "type": "JSON", "MST": mst}
    data = get_json(SERVICE_URL, params, RAW / f"law_{mst}.json")
    return data.get("법령", data)


def articles(law: dict) -> list[dict]:
    """조문 목록. '전문'(장·절 제목)은 빼고 '조문'만 남긴다."""
    units = law.get("조문", {}).get("조문단위", [])
    if isinstance(units, dict):
        units = [units]
    return [u for u in units if u.get("조문여부", "조문") == "조문"]


def main() -> None:
    if not OC:
        raise SystemExit("LAW_OC가 없습니다. .env를 확인하세요.")
    for name in LAWS:
        item = find_law(name)
        if not item:
            continue
        mst = item.get("법령일련번호")
        print(f"\n[{name}] MST={mst} 시행일자={item.get('시행일자')}")
        law = fetch_law(mst)
        arts = articles(law)
        if not arts:
            # 응답 형태가 예상과 다르면 최상위 키를 보여 준다. 여기를 보고 articles()를 고친다.
            print("  조문을 찾지 못했습니다. 응답 키:", list(law.keys())[:10])
            continue
        print(f"  조문 {len(arts)}개")
        for a in arts[:5]:
            text = str(a.get("조문내용", "")).replace("\n", " ")
            print(f"  - 제{a.get('조문번호')}조 {a.get('조문제목', '')}: {text[:60]}")


if __name__ == "__main__":
    main()
