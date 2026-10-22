"""판례 건수 확인 · 결측률 · 쟁점 분류 · 최종 항목 선정 (v2)

준비:
    pip install requests python-dotenv
    .env 파일에  LAW_OC=발급받은_인증키  (소스코드에 키를 적지 않는다: NF2003)

실행 (항상 프로젝트 폴더에서):
    python check_precedents.py                  # 1단계: 목록만 조회해 건수 확인표 출력
    python check_precedents.py --detail 600     # 2단계: 후보 본문 조회 + 결측률 + 분류 + 선정
    python check_precedents.py --reanalyze      # 네트워크 없이 data/precedents_checked.csv로 분류·선정만 다시

v1과 달라진 점
    1. 참조조문에 주택/상가건물 임대차보호법이 없는 판례는 제외한다. (민법, 파산법 등)
    2. 후보 조회 순서를 쟁점이 적은 검색어(상가, 계약갱신요구권, 임차권등기) 먼저로 바꿨다.
    3. 참조조문을 법령별 조문 단위로 파싱한다. (v1은 다른 법의 '제3조'를 잘못 셀 수 있었다)
    4. 보증금 반환 쟁점은 조문 규칙에 사건명·판시사항 텍스트 규칙을 보조로 더했다.
       어느 규칙으로 붙었는지 '쟁점출처' 열에 남기니 CSV를 열어 직접 확인할 것.
    5. 쟁점별 상한(--cap)으로 최종 항목을 선정한다. 상가를 먼저 담고, 나머지는 최신 선고순.
    6. 호출 실패 시 재시도(3회, 대기 시간 증가). 오류 메시지에는 인증키가 든 URL을 찍지 않는다.

결과:
    data/raw/                      API 응답 캐시 (Git 제외. 판례상세링크에 인증키가 들어 있음)
    data/precedents_checked.csv    조회한 모든 판례와 분류 결과 (판단용)
    data/items_selected.json       최종 선정 항목 (색인 단계의 입력)
"""

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # --reanalyze는 python-dotenv 없이도 동작한다
    pass

OC = os.environ.get("LAW_OC")
LIST_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"
HEADERS = {"User-Agent": "law-rag-student-project/1.0"}

DATA = Path("data")
RAW = DATA / "raw"
CHECKED_CSV = DATA / "precedents_checked.csv"
ITEMS_JSON = DATA / "items_selected.json"

# 쟁점이 적은 검색어를 앞에 둔다. 앞 N건만 본문을 받아도 쟁점이 고르게 섞이도록.
QUERIES = [
    "상가건물 임대차보호법",
    "계약갱신요구권",
    "임차권등기",
    "주택임대차보호법",
    "대항력",
    "보증금 반환",
]
SLEEP = 0.4  # 호출 간격(초). NF2006
RETRIES = 3

ISSUES = ["대항력", "우선변제권", "보증금반환", "계약갱신"]

# 쟁점 분류 규칙: (쟁점, 법 구분, 조문 집합).
# 조문 번호는 법령 본문으로 확인한 뒤 조정할 것. 특히 상가 제10조의2~9는 권리금 조문이 섞여 있을 수 있다.
RULES = [
    ("대항력", "주택", {"제3조"}),
    ("우선변제권", "주택", {"제3조의2", "제8조"}),
    ("보증금반환", "주택", {"제3조의3", "제3조의4"}),
    ("계약갱신", "주택", {"제6조", "제6조의2", "제6조의3"}),
    ("대항력", "상가", {"제3조"}),
    ("우선변제권", "상가", {"제5조", "제14조"}),
    ("보증금반환", "상가", {"제6조"}),
    ("계약갱신", "상가", {"제10조"} | {f"제10조의{n}" for n in range(2, 10)}),
]
TEXT_DEPOSIT = re.compile(r"임대차보증금|보증금\s*반환|보증금반환")  # 보증금반환 보조 규칙

# 참조조문 파서: 법령명(구 법령의 괄호 설명 포함)과 조문 번호를 순서대로 읽는다.
LAW_NAME = r"[가-힣]+(?:\s[가-힣]+)*?(?:법률|법)(?:\s시행령|\s시행규칙)?"
TOKEN = re.compile(rf"(?P<law>{LAW_NAME}(?:\([^)]*\))?)(?=\s*제\d)|(?P<art>제\d+조(?:의\d+)?)")
HOUSE = re.compile(r"주택\s*임대차보호법$")
SHOP = re.compile(r"상가건물\s*임대차보호법$")

CSV_COLUMNS = [
    "판례일련번호", "사건번호", "사건명", "법원명", "선고일자", "선고연도",
    "판시사항", "판결요지", "참조조문",
    "임대차보호법", "구분", "쟁점", "쟁점출처", "선정", "대표쟁점",
]


# ---------------------------------------------------------------- API 호출
def get_json(url: str, params: dict, cache: Path) -> dict:
    """캐시가 있으면 재호출하지 않는다 (NF2002). 실패 시 재시도한다 (NF2006)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import requests

    last = None
    for attempt in range(RETRIES):
        try:
            res = requests.get(url, params=params, timeout=20, headers=HEADERS)
            res.raise_for_status()
            data = res.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last = type(exc).__name__  # 예외 문장에는 인증키가 든 URL이 있을 수 있어 이름만 남긴다
            time.sleep(SLEEP * 2 ** (attempt + 1))
    else:
        raise RuntimeError(f"호출 실패({last})")
    RAW.mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------- 분류 · 선정
def clean(text) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", str(text))
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_refs(ref: str) -> list[tuple[str, str]]:
    """참조조문 문자열을 (법령명, 조문) 쌍으로 푼다. 법령명이 생략된 조문은 직전 법령에 속한다."""
    law, out = None, []
    for m in TOKEN.finditer(ref):
        if m.group("law"):
            law = re.sub(r"\([^)]*\)", "", m.group("law")).strip()
        elif law:
            out.append((law, m.group("art")))
    return out


def enrich(row: dict) -> dict:
    """분류 결과 열을 채운다. 임대차보호법 관련 여부, 주택/상가 구분, 쟁점, 쟁점 출처."""
    kinds, issues, source = set(), [], {}
    for law, art in parse_refs(row["참조조문"]):
        if HOUSE.search(law):
            kind = "주택"
        elif SHOP.search(law):
            kind = "상가"
        else:
            continue
        kinds.add(kind)
        for issue, rule_kind, arts in RULES:
            if rule_kind == kind and art in arts and issue not in issues:
                issues.append(issue)
                source[issue] = "조문"
    if kinds and "보증금반환" not in issues and TEXT_DEPOSIT.search(row["사건명"] + " " + row["판시사항"]):
        issues.append("보증금반환")
        source["보증금반환"] = "텍스트"

    digits = re.sub(r"\D", "", row["선고일자"])[:8]
    row["선고연도"] = int(digits[:4]) if len(digits) >= 4 else 0
    row["_date"] = int(digits) if digits else 0
    row["_issues"] = issues
    row["_complete"] = bool(row["판시사항"] and row["판결요지"] and row["참조조문"])
    row["임대차보호법"] = "Y" if kinds else "N"
    row["구분"] = "+".join(sorted(kinds)) if kinds else "불명"
    row["쟁점"] = "/".join(issues) if issues else "미분류"
    row["쟁점출처"] = "/".join(f"{i}:{s}" for i, s in source.items())
    row["선정"] = ""
    row["대표쟁점"] = ""
    return row


def select_items(usable: list[dict], cap: int) -> dict[str, str]:
    """쟁점별 상한(cap)으로 고른다. 건수가 적은 쟁점부터 채우고, 상가를 먼저 담은 뒤 최신 선고순."""
    counts = {i: sum(i in r["_issues"] for r in usable) for i in ISSUES}
    chosen: dict[str, str] = {}
    for issue in sorted(ISSUES, key=lambda i: counts[i]):
        pool = [r for r in usable if issue in r["_issues"] and r["판례일련번호"] not in chosen]
        pool.sort(key=lambda r: (0 if "상가" in r["구분"] else 1, -r["_date"]))
        for r in pool[:cap]:
            chosen[r["판례일련번호"]] = issue
    return chosen


# ---------------------------------------------------------------- 출력
def year_bucket(year: int) -> str:
    return "2009년 이전" if year <= 2009 else ("2010~2019" if year <= 2019 else "2020년 이후")


def print_report(rows: list[dict], chosen: dict[str, str]) -> None:
    total = len(rows)
    print(f"\n본문 {total}건 기준")
    for col in ("판시사항", "판결요지", "참조조문"):
        miss = sum(1 for r in rows if not r[col])
        print(f"- {col} 결측: {miss}건 ({miss / total:.0%})")
    complete = [r for r in rows if r["_complete"]]
    usable = [r for r in complete if r["임대차보호법"] == "Y"]
    print(f"- 세 필드가 모두 있는 항목: {len(complete)}건")
    print(f"- 그중 임대차보호법 관련(참조조문에 법령 있음): {len(usable)}건  <- 100건 이상이어야 함")
    print(f"- 임대차보호법 언급 없음으로 제외: {len(complete) - len(usable)}건")

    print("\n[사용 가능 항목] 구분 × 쟁점 (복수 쟁점은 각각 집계)")
    for kind in sorted({r["구분"] for r in usable}):
        sub = [r for r in usable if r["구분"] == kind]
        cnt = {i: sum(i in r["_issues"] for r in sub) for i in ISSUES}
        none = sum(1 for r in sub if not r["_issues"])
        print(f"- {kind} {len(sub)}건: {cnt}, 미분류 {none}")
    total_cnt = {i: sum(i in r["_issues"] for r in usable) for i in ISSUES}
    text_only = sum(1 for r in usable if "보증금반환:텍스트" in r["쟁점출처"])
    print(f"- 전체: {total_cnt}  (보증금반환 중 텍스트 규칙으로만 붙은 항목 {text_only}건)")

    buckets: dict[str, int] = {}
    for r in usable:
        buckets[year_bucket(r["선고연도"])] = buckets.get(year_bucket(r["선고연도"]), 0) + 1
    print(f"- 선고연도: {dict(sorted(buckets.items()))}")

    body = {r["판례일련번호"]: r["판시사항"] + " " + r["판결요지"] for r in usable}
    print("\n[질문 근거 후보 건수] (키워드 추정치. 정답 근거표를 만들 때 개별 확인할 것)")
    checks = {
        "Q1 근저당+대항력": lambda r: "근저당" in body[r["판례일련번호"]] and "제3조" in r["참조조문"],
        "Q2 환산보증금": lambda r: "환산보증금" in body[r["판례일련번호"]],
        "Q3 주택 제6조의3": lambda r: "제6조의3" in r["참조조문"],
        "Q4 묵시적 갱신": lambda r: "묵시적" in body[r["판례일련번호"]],
        "Q5 소액임차인": lambda r: "소액임차인" in body[r["판례일련번호"]] or "제8조" in r["참조조문"],
        "Q6 법인 임차인": lambda r: "법인" in body[r["판례일련번호"]],
    }
    for name, fn in checks.items():
        hit = [r for r in usable if fn(r)]
        recent = sum(1 for r in hit if r["선고연도"] >= 2020)
        print(f"- {name}: {len(hit)}건 (2020년 이후 {recent}건)")

    if chosen:
        picked = [r for r in usable if r["판례일련번호"] in chosen]
        by_issue = {i: sum(1 for r in picked if chosen[r["판례일련번호"]] == i) for i in ISSUES}
        by_kind = {k: sum(1 for r in picked if r["구분"] == k) for k in sorted({r["구분"] for r in picked})}
        by_year: dict[str, int] = {}
        for r in picked:
            by_year[year_bucket(r["선고연도"])] = by_year.get(year_bucket(r["선고연도"]), 0) + 1
        print(f"\n[최종 선정] {len(picked)}건")
        print(f"- 대표쟁점별: {by_issue}")
        print(f"- 구분별: {by_kind}")
        print(f"- 선고연도별: {dict(sorted(by_year.items()))}")


def save_outputs(rows: list[dict], chosen: dict[str, str]) -> None:
    DATA.mkdir(exist_ok=True)
    for r in rows:
        if r["판례일련번호"] in chosen:
            r["선정"] = "Y"
            r["대표쟁점"] = chosen[r["판례일련번호"]]
    with open(CHECKED_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    selected = [
        {
            "판례일련번호": r["판례일련번호"], "사건번호": r["사건번호"], "사건명": r["사건명"],
            "법원명": r["법원명"], "선고일자": r["선고일자"], "구분": r["구분"],
            "쟁점": r["_issues"], "대표쟁점": r["대표쟁점"],
            "판시사항": r["판시사항"], "판결요지": r["판결요지"], "참조조문": r["참조조문"],
        }
        for r in rows if r["선정"] == "Y"
    ]
    ITEMS_JSON.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {CHECKED_CSV} ({len(rows)}건), {ITEMS_JSON} ({len(selected)}건)")


# ---------------------------------------------------------------- 실행
def load_rows_from_csv() -> list[dict]:
    if not CHECKED_CSV.exists():
        raise SystemExit(f"{CHECKED_CSV}가 없습니다. 먼저 --detail로 본문을 조회하세요.")
    rows = []
    with open(CHECKED_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "판례일련번호": str(r.get("판례일련번호", "")),
                "사건번호": r.get("사건번호", ""),
                "사건명": r.get("사건명", "") or "",
                "법원명": r.get("법원명", "") or "대법원",
                "선고일자": r.get("선고일자", "") or "",
                "판시사항": r.get("판시사항", "") or "",
                "판결요지": r.get("판결요지", "") or "",
                "참조조문": r.get("참조조문", "") or "",
            })
    return rows


def collect_rows(args: argparse.Namespace) -> list[dict]:
    if not OC:
        raise SystemExit("LAW_OC가 없습니다. .env를 확인하세요.")
    pool: dict[str, dict] = {}
    print("| 검색어 | 전체 건수 | 민사 | 대법원 민사 |")
    print("| --- | --- | --- | --- |")
    for q in QUERIES:
        total, items = fetch_list(q, args.search)
        civil = [x for x in items if x.get("사건종류명") == "민사"]
        supreme = [x for x in civil if x.get("법원명") == "대법원"]
        print(f"| {q} | {total} | {len(civil)} | {len(supreme)} |")
        for x in supreme:
            pool.setdefault(x["판례일련번호"], x)
    print(f"\n검색어 간 중복 제거 후 대법원 민사 후보: {len(pool)}건 (조회 순서: {' > '.join(QUERIES)})")
    if not args.detail:
        print("본문 결측률과 분류는 --detail 600 처럼 실행해서 확인하세요.")
        return []

    rows, failed = [], []
    targets = list(pool.items())[: args.detail]
    for n, (cid, meta) in enumerate(targets, start=1):
        try:
            d = fetch_detail(cid)
        except RuntimeError as exc:
            failed.append((cid, str(exc)))
            continue
        rows.append({
            "판례일련번호": cid, "사건번호": meta.get("사건번호", ""), "사건명": meta.get("사건명", "") or "",
            "법원명": meta.get("법원명", ""), "선고일자": meta.get("선고일자", "") or "",
            "판시사항": clean(d.get("판시사항")), "판결요지": clean(d.get("판결요지")),
            "참조조문": clean(d.get("참조조문")),
        })
        if n % 50 == 0:
            print(f"  본문 조회 {n}/{len(targets)}", flush=True)
    if failed:
        print(f"\n본문 조회 실패 {len(failed)}건: {[c for c, _ in failed][:10]} ... 다시 실행하면 이어서 받습니다.")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", type=int, default=0, help="본문 조회할 최대 건수 (0이면 목록만)")
    parser.add_argument("--search", type=int, default=2, help="1=판례명 검색, 2=본문 검색")
    parser.add_argument("--cap", type=int, default=40, help="쟁점별 최종 선정 상한")
    parser.add_argument("--reanalyze", action="store_true", help="네트워크 없이 기존 CSV로 분류·선정만 다시")
    args = parser.parse_args()

    rows = load_rows_from_csv() if args.reanalyze else collect_rows(args)
    if not rows:
        return
    rows = [enrich(r) for r in rows]
    usable = [r for r in rows if r["_complete"] and r["임대차보호법"] == "Y"]
    chosen = select_items(usable, args.cap)
    print_report(rows, chosen)
    save_outputs(rows, chosen)


if __name__ == "__main__":
    main()
