"""법제처 Open API 수집 (F2004). 도메인에 묶인 수집 코드는 data_prep에 모은다 (NF2005).

- get_json()            캐시 → 재시도 → 호출 간격 (NF2002, NF2006)
- load_law()            법령 이름으로 현행법령 본문을 받는다
- law_articles()        법령 본문 → 조문 목록 (항·호·목까지 펼친 본문)
- precedent_fulltext()  판결문 전문 (check_precedents.py가 받아 둔 캐시를 먼저 쓴다)
- search_expc()         법령해석례 검색 (config.USE_EXPC가 True일 때만)
"""

import json
import os
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

import config

load_dotenv()

HEADERS = {"User-Agent": "law-rag-student-project/1.0"}

# 이 서비스가 근거로 쓰는 법령. 정의서 3장.
LAWS = [
    "주택임대차보호법",
    "주택임대차보호법 시행령",
    "상가건물 임대차보호법",
    "상가건물 임대차보호법 시행령",
]


class LawApiError(RuntimeError):
    """키 누락이나 호출 실패. 메시지에 인증키가 든 URL을 넣지 않는다 (NF2004)."""


# ---------------------------------------------------------------- API 호출
def _oc() -> str:
    oc = os.environ.get("LAW_OC", "").strip()
    if not oc:
        raise LawApiError("LAW_OC가 없습니다. .env를 확인하세요.")
    return oc


def get_json(url: str, params: dict, cache: Path) -> dict:
    """캐시가 있으면 재호출하지 않는다 (NF2002). 실패 시 재시도한다 (NF2006)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import requests

    params = {"OC": _oc(), "type": "JSON", **params}
    last = None
    for attempt in range(config.API_RETRIES):
        try:
            res = requests.get(url, params=params, timeout=20, headers=HEADERS)
            res.raise_for_status()
            data = res.json()
            if is_error_response(data):  # 오류 응답은 캐시에 저장하지 않는다. 저장하면 다시 받을 기회가 사라진다
                raise LawApiError(f"법제처 응답: {str(data['Law'])[:80]}")
            break
        except (requests.RequestException, ValueError) as exc:
            last = type(exc).__name__  # 예외 문장에는 인증키가 든 URL이 있을 수 있어 이름만 남긴다
            time.sleep(config.API_SLEEP * 2 ** (attempt + 1))
    else:
        raise LawApiError(f"법제처 API 호출 실패({last})")
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(config.API_SLEEP)
    return data


def is_error_response(data: dict) -> bool:
    """법제처는 찾는 자료가 없거나 권한이 없으면 {"Law": "안내 문장"}만 돌려준다."""
    return isinstance(data, dict) and list(data.keys()) == ["Law"]


def clean(text) -> str:
    if not text:
        return ""
    if isinstance(text, list):
        text = "\n".join(str(t) for t in text)
    text = re.sub(r"<br\s*/?>", "\n", str(text))
    return re.sub(r"<[^>]+>", "", text).strip()


# ---------------------------------------------------------------- 법령
def load_law(name: str) -> dict:
    """법령 이름 → {'name', 'mst', 'effective', 'body'}"""
    data = get_json(config.LAW_LIST_URL, {"target": "law", "query": name},
                    config.RAW_DIR / f"law_search_{name}.json")
    items = data.get("LawSearch", {}).get("law", [])
    if isinstance(items, dict):
        items = [items]
    found = next((it for it in items if it.get("법령명한글") == name), None)
    if not found:
        raise LawApiError(f"'{name}'을(를) 찾지 못했습니다.")
    mst = str(found["법령일련번호"])
    body = get_json(config.LAW_DETAIL_URL, {"target": "law", "MST": mst},
                    config.RAW_DIR / f"law_{mst}.json")
    return {"name": name, "mst": mst, "effective": str(found.get("시행일자", "")),
            "body": body.get("법령", body)}


def _as_list(node) -> list:
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def _flatten(unit: dict) -> list[str]:
    """조문 → 항 → 호 → 목 순서로 '...내용' 문장을 모두 꺼낸다.
    항이 있는 조문은 조문내용에 제목만 있고 본문은 항에 있다."""
    lines = []
    for key in ("조문내용", "항내용", "호내용", "목내용"):
        if key in unit:
            text = clean(unit[key])
            if text:
                lines.append(text)
    for child in ("항", "호", "목"):
        for sub in _as_list(unit.get(child)):
            if isinstance(sub, dict):
                lines.extend(_flatten(sub))
    return lines


ARTICLE_LABEL = re.compile(r"^\s*(제\d+조(?:의\d+)?)")


def article_label(unit: dict) -> str:
    """'제3조의2'. 조문번호에는 가지번호가 빠져 있어 조문내용 앞부분에서 먼저 읽는다."""
    m = ARTICLE_LABEL.match(clean(unit.get("조문내용", "")))
    if m:
        return m.group(1)
    label = f"제{unit.get('조문번호', '')}조"
    branch = str(unit.get("조문가지번호", "") or "").strip()
    return f"{label}의{branch}" if branch and branch != "0" else label


def law_url(law_name: str, label: str) -> str:
    """인증키 없는 조문 원문 주소. TODO: 브라우저에서 열리는지 확인할 것."""
    return f"https://www.law.go.kr/법령/{quote(law_name.replace(' ', ''))}/{quote(label)}"


def law_articles(law: dict) -> list[dict]:
    """조문 1개 = 문서 1개. '전문'(장·절 제목)은 뺀다."""
    units = _as_list(law["body"].get("조문", {}).get("조문단위"))
    out = []
    for unit in units:
        if unit.get("조문여부", "조문") != "조문":
            continue
        label = article_label(unit)
        title = clean(unit.get("조문제목", ""))
        out.append({
            "doc_type": "법령",
            "doc_id": f"law{law['mst']}-{label}",
            "title": f"{law['name']} {label}" + (f"({title})" if title else ""),
            "law_name": law["name"],
            "article_no": label,
            "effective_date": law["effective"],
            "text": "\n".join(_flatten(unit)),
            "source_url": law_url(law["name"], label),
        })
    return out


def all_articles() -> list[dict]:
    """LAWS의 모든 조문. 법령 본문은 캐시에 있으면 다시 받지 않는다."""
    out = []
    for name in LAWS:
        out.extend(law_articles(load_law(name)))
    return out


# ---------------------------------------------------------------- 판결문 전문
def precedent_fulltext(item: dict) -> dict:
    """선택한 판례 자체를 다룬 문서 (F2003, F2004: 항상 색인).

    판결문 전문을 먼저 쓴다. check_precedents.py의 캐시(detail_{id}.json)가 오류 응답이면 지우고 한 번 다시 받는다.
    법제처가 전문을 주지 않는 판례는 판시사항·판결요지·참조조문으로 대신하고, 제목에 그 사실을 밝힌다.
    """
    from data_prep.precedents import source_url  # 순환 import를 피하려고 여기서 읽는다

    case_id = item["판례일련번호"]
    cache = config.RAW_DIR / f"detail_{case_id}.json"
    if cache.exists() and is_error_response(json.loads(cache.read_text(encoding="utf-8"))):
        cache.unlink()
    text, reason = "", ""
    try:
        data = get_json(config.LAW_DETAIL_URL, {"target": "prec", "ID": case_id}, cache)
        text = clean(data.get("PrecService", data).get("판례내용"))
    except LawApiError as exc:
        reason = str(exc)
    base = {"doc_type": "판결문", "doc_id": f"prec{case_id}", "case_id": case_id,
            "effective_date": item.get("선고일자", ""), "source_url": source_url(case_id)}
    if text:
        return {**base, "title": f"{item['법원명']} {item['사건번호']} 판결문", "text": text}
    print(f"  [안내] {item['사건번호']} 판결문 전문을 받지 못해 판시사항·판결요지로 대신합니다. {reason}")
    fallback = f"판시사항\n{item['판시사항']}\n\n판결요지\n{item['판결요지']}\n\n참조조문\n{item['참조조문']}"
    return {**base, "title": f"{item['법원명']} {item['사건번호']} 판결 요지 (전문 미제공)", "text": fallback}


# ---------------------------------------------------------------- 법령해석례 (선택)
def search_expc(query: str, limit: int = 5) -> list[dict]:
    """해석례 후보 목록 (제목만). 본문은 선별된 것만 받는다."""
    if not config.USE_EXPC:
        return []
    data = get_json(config.LAW_LIST_URL, {"target": "expc", "query": query, "display": limit},
                    config.RAW_DIR / f"expc_search_{query}.json")
    items = _as_list(data.get("Expc", {}).get("expc"))
    return [{"doc_type": "해석례", "doc_id": f"expc{it.get('법령해석례일련번호')}",
             "expc_id": str(it.get("법령해석례일련번호")), "title": f"해석례: {it.get('안건명', '')}",
             "text": ""} for it in items[:limit]]


def expc_body(cand: dict) -> dict:
    data = get_json(config.LAW_DETAIL_URL, {"target": "expc", "ID": cand["expc_id"]},
                    config.RAW_DIR / f"expc_{cand['expc_id']}.json")
    data = data.get("ExpcService", data)
    parts = [clean(data.get(k)) for k in ("질의요지", "회답", "이유")]
    return {**cand, "text": "\n\n".join(p for p in parts if p),
            "source_url": f"https://www.law.go.kr/LSW/expcInfoP.do?expcSeq={cand['expc_id']}"}


def today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- 쟁점 태그
def article_issues(law_name: str, label: str) -> list[str]:
    """조문 → 쟁점. check_precedents.py의 분류 규칙을 그대로 쓴다. 시행령은 법 본문 규칙을 따르지 않는다."""
    from check_precedents import RULES

    if law_name.endswith("시행령"):
        return []
    kind = "상가" if law_name.startswith("상가") else "주택"
    return [issue for issue, rule_kind, arts in RULES if rule_kind == kind and label in arts]
