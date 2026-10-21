"""판례 건수 확인표 + 결측률 계산 (조건 1 점검용)

준비:
    pip install requests python-dotenv
    .env 파일에  LAW_OC=발급받은_인증키  (소스코드에 키를 적지 않는다: NF2003)

실행:
    python check_precedents.py              # 1단계: 목록만 조회해 건수 확인표 출력
    python check_precedents.py --detail 150 # 2단계: 대법원 민사 후보 최대 150건 본문 조회 + 결측률 + 쟁점 분류

결과:
    data/raw/list_{검색어}_p{n}.json   목록 응답 캐시
    data/raw/detail_{판례일련번호}.json 본문 응답 캐시 (재실행해도 다시 호출하지 않는다: NF2002)
    data/precedents_checked.csv        판단용 표
"""

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
OC = os.environ.get("LAW_OC")
LIST_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"
RAW = Path("data/raw")
RAW.mkdir(parents=True, exist_ok=True)

QUERIES = [
    "주택임대차보호법",
    "상가건물 임대차보호법",
    "대항력",
    "계약갱신요구권",
    "임차권등기",
    "보증금 반환",
]
SLEEP = 0.4  # 호출 간격(초). NF2006

# 참조조문 기반 쟁점 분류 규칙 (초안: 실제 데이터를 보고 고친다)
# (쟁점, 법 구분, 조문 패턴)
RULES = [
    ("대항력", "주택", r"제3조(?!의)"),
    ("우선변제권", "주택", r"제3조의2|제8조"),
    ("보증금반환", "주택", r"제3조의3|제3조의4"),
    ("계약갱신", "주택", r"제6조(?!의)|제6조의2|제6조의3"),
    ("대항력", "상가", r"제3조(?!의)"),
    ("우선변제권", "상가", r"제5조|제14조"),
    ("보증금반환", "상가", r"제6조"),
    ("계약갱신", "상가", r"제10조(?!의)|제10조의[2-9]"),
]
LAW_PAT = re.compile(r"(주택\s*임대차보호법|상가건물\s*임대차보호법)")


def clean(text) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", str(text))
    return re.sub(r"<[^>]+>", "", text).strip()


def get_json(url: str, params: dict, cache: Path) -> dict:
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    data = res.json()
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(SLEEP)
    return data


def fetch_list(query: str, search: int) -> tuple[int, list[dict]]:
    """한 검색어의 전체 목록을 페이지 끝까지 가져온다. (전체건수, 항목들)"""
    items, page, total = [], 1, 0
    while True:
        params = {"OC": OC, "target": "prec", "type": "JSON", "query": query,
                  "display": 100, "page": page, "search": search}
        cache = RAW / f"list_{query}_s{search}_p{page}.json"
        body = get_json(LIST_URL, params, cache).get("PrecSearch", {})
        total = int(body.get("totalCnt", 0) or 0)
        batch = body.get("prec", [])
        if isinstance(batch, dict):
            batch = [batch]
        items += batch
        if not batch or len(items) >= total:
            break
        page += 1
    return total, items


def fetch_detail(case_id: str) -> dict:
    params = {"OC": OC, "target": "prec", "type": "JSON", "ID": case_id}
    data = get_json(DETAIL_URL, params, RAW / f"detail_{case_id}.json")
    return data.get("PrecService", data)


def classify(ref: str) -> tuple[str, list[str]]:
    """참조조문에서 (주택/상가/불명, 쟁점 목록)을 뽑는다."""
    parts = list(LAW_PAT.finditer(ref))
    kinds, issues = set(), []
    for i, m in enumerate(parts):
        kind = "상가" if m.group(1).startswith("상가") else "주택"
        kinds.add(kind)
        end = parts[i + 1].start() if i + 1 < len(parts) else len(ref)
        segment = ref[m.end():end]
        for issue, rule_kind, pat in RULES:
            if rule_kind == kind and re.search(pat, segment) and issue not in issues:
                issues.append(issue)
    kind = "+".join(sorted(kinds)) if kinds else "불명"
    return kind, issues


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", type=int, default=0, help="본문 조회할 최대 건수(0이면 목록만)")
    parser.add_argument("--search", type=int, default=2, help="1=판례명 검색, 2=본문 검색")
    args = parser.parse_args()
    if not OC:
        raise SystemExit("LAW_OC가 없습니다. .env를 확인하세요.")

    # ---------- 1단계: 건수 확인표 ----------
    pool: dict[str, dict] = {}
    print("| 검색어 | 전체 건수 | 민사 | 대법원 민사 |")
    print("| --- | --- | --- | --- |")
    for q in QUERIES:
        total, items = fetch_list(q, args.search)
        civil = [x for x in items if x.get("사건종류명") == "민사"]
        supreme = [x for x in civil if x.get("법원명") == "대법원"]
        print(f"| {q} | {total} | {len(civil)} | {len(supreme)} |")
        for x in supreme:
            pool[x["판례일련번호"]] = x
    print(f"\n검색어 간 중복 제거 후 대법원 민사 후보: {len(pool)}건")
    if not args.detail:
        print("본문 결측률은 --detail 150 처럼 실행해서 확인하세요.")
        return

    # ---------- 2단계: 본문 조회, 결측률, 쟁점 분류 ----------
    rows = []
    for n, (cid, meta) in enumerate(pool.items(), start=1):
        if n > args.detail:
            break
        d = fetch_detail(cid)
        ref = clean(d.get("참조조문"))
        kind, issues = classify(ref)
        rows.append({
            "판례일련번호": cid,
            "사건번호": meta.get("사건번호"),
            "사건명": meta.get("사건명"),
            "선고일자": meta.get("선고일자"),
            "판시사항": clean(d.get("판시사항")),
            "판결요지": clean(d.get("판결요지")),
            "참조조문": ref,
            "구분": kind,
            "쟁점": "/".join(issues) if issues else "미분류",
        })

    total = len(rows)
    print(f"\n본문 조회 {total}건 기준")
    for col in ("판시사항", "판결요지", "참조조문"):
        miss = sum(1 for r in rows if not r[col])
        print(f"- {col} 결측: {miss}건 ({miss / total:.0%})")
    usable = [r for r in rows if r["판시사항"] and r["판결요지"] and r["참조조문"]]
    print(f"- 셋 다 있는 항목: {len(usable)}건  (100건 이상이어야 함)")

    print("\n쟁점 × 구분 분포 (사용 가능 항목 기준)")
    dist: dict[tuple, int] = {}
    for r in usable:
        for issue in r["쟁점"].split("/"):
            dist[(issue, r["구분"])] = dist.get((issue, r["구분"]), 0) + 1
    for (issue, kind), cnt in sorted(dist.items()):
        print(f"- {issue} / {kind}: {cnt}건")

    with open("data/precedents_checked.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print("\n저장: data/precedents_checked.csv")


if __name__ == "__main__":
    main()