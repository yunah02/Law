"""도메인 서비스 기능 (F2010, F2011).

- find_precedents()     조건 기반 판례 찾기: 자연어 조건 → 의미 검색 → LLM이 조건에 맞는 판례만 고름
- check_requirements()  요건 점검: 쟁점의 요건을 법령·판례 근거와 함께 하나씩 점검 (결과 예측 없음)
"""

import config
from collect import index_docs, parse_json
from data_prep import law_api
from graph import NOT_FOUND, PRECEDENT_NOTE, cited_sources, format_context, tidy
from prompts import CHECK_PROMPT, FILTER_PROMPT
from retrieval import _evidence

ISSUES = ["대항력", "우선변제권", "보증금반환", "계약갱신"]


# ---------------------------------------------------------------- 조건 기반 판례 찾기 (F2011)
def find_precedents(condition: str, prec_store, model, k: int = 20, limit: int = 8) -> list[dict]:
    """[{case_id, display_name, reason, kind, issues}]. 조건에 맞는 판례가 없으면 빈 목록."""
    kinds = [kd for kd in ("주택", "상가") if kd in condition]
    flt = {"kind": kinds[0]} if len(kinds) == 1 else None  # 조건에 주택/상가가 하나만 있으면 그 종류만 찾는다
    docs = prec_store.similarity_search(condition, k=k, filter=flt)
    if not docs:
        return []
    lines = []
    for n, doc in enumerate(docs, start=1):
        m = doc.metadata
        body = doc.page_content.split("\n", 2)[-1][:300].replace("\n", " ")  # 첫 줄(사건명) 다음부터
        lines.append(f"{n}. [{m['kind']}] [{m['issues']}] {m['case_name']}\n   {body}")
    try:
        picked = parse_json(model.invoke(FILTER_PROMPT.format(condition=condition, candidates="\n".join(lines),
                                                               limit=limit)).content)
    except Exception:
        return []
    out, seen = [], set()
    for p in picked if isinstance(picked, list) else []:
        try:
            doc = docs[int(p["no"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        m = doc.metadata
        if m["case_id"] in seen:
            continue
        seen.add(m["case_id"])
        out.append({"case_id": m["case_id"], "display_name": m["display_name"], "reason": str(p.get("reason", "")),
                    "kind": m["kind"], "issues": m["issues"]})
    return out[:limit]


# ---------------------------------------------------------------- 요건 점검 (F2010)
def issue_articles(issue: str, kind: str) -> list[dict]:
    """쟁점에 해당하는 조문 (check_precedents.py의 분류 규칙 기준)."""
    return [a for a in law_api.all_articles()
            if a["law_name"].startswith(kind) and issue in law_api.article_issues(a["law_name"], a["article_no"])]


def check_requirements(issue: str, kind: str, situation: str, item: dict | None,
                       model, prec_store, doc_store) -> tuple[str, list[dict]]:
    """(점검 결과, 근거 목록). 근거 = 쟁점 조문 + 선택한 판례(없으면 같은 쟁점의 비슷한 판례 2건)."""
    articles = issue_articles(issue, kind)
    index_docs(doc_store, [{**a, "query": f"(요건 점검: {issue})"} for a in articles], lambda d: "common")

    evidence = []
    if item:
        got = prec_store.get(ids=[item["판례일련번호"]])
        if got["ids"]:
            from langchain_core.documents import Document

            evidence.append(_evidence(got["ids"][0], Document(page_content=got["documents"][0],
                                                              metadata=got["metadatas"][0])))
    else:
        hits = prec_store.similarity_search(f"{kind} {issue} {situation}", k=8)
        same = [d for d in hits if issue in d.metadata.get("issues", "") and kind in d.metadata.get("kind", "")]
        evidence += [_evidence(d.metadata["case_id"], d) for d in same[:2]]
    doc_ids = [a["doc_id"] for a in articles]
    if doc_ids:  # 긴 조문은 청크가 여러 개라 doc_id로 모두 가져온다
        got = doc_store.get(where={"doc_id": {"$in": doc_ids}})
        from langchain_core.documents import Document

        for doc_id, text, meta in zip(got["ids"], got["documents"], got["metadatas"]):
            evidence.append(_evidence(doc_id, Document(page_content=text, metadata=meta)))

    if not evidence:
        return NOT_FOUND, []
    prompt = CHECK_PROMPT.format(issue=issue, kind=kind, situation=situation or "(입력 없음)",
                                 context=format_context(evidence))
    answer = tidy(model.invoke(prompt).content.strip())
    if any(ev["source_type"] in ("판례", "판결문") for _, ev in cited_sources(answer, evidence)):
        answer += "\n\n" + PRECEDENT_NOTE
    return answer, evidence
