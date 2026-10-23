"""근거 검색과 병합 (F2006, F2014).

- 선택한 판례의 메타데이터 청크는 검색 없이 항상 근거 1번으로 넣는다.
- legal_docs에서 질문과 비슷한 청크를 Top-k개 찾는다. 판례를 골랐으면 그 판례의 판결문과 공통 문서(법령·해석례)만 본다.
- 두 결과를 합치고 같은 청크(같은 ID 또는 같은 본문)를 한 번만 남긴다.
"""

import config

LABELS = {"판례": "판례 메타데이터", "법령": "법령", "판결문": "판결문", "해석례": "해석례"}


def _evidence(doc_id: str, doc) -> dict:
    meta = doc.metadata
    return {
        "id": doc_id,
        "source_type": meta.get("source_type", ""),
        "label": LABELS.get(meta.get("source_type", ""), meta.get("source_type", "")),
        "title": meta.get("title") or meta.get("display_name", ""),
        "url": meta.get("source_url", ""),
        "date": meta.get("date") or meta.get("effective_date", ""),
        "text": doc.page_content,
        "law_name": meta.get("law_name", ""),
    }


def _other_law(ev: dict, item: dict | None) -> bool:
    """선택한 판례가 상가 사건이면 주택법 조문을, 주택 사건이면 상가법 조문을 근거에서 뺀다."""
    if not item or ev["source_type"] != "법령":
        return False
    kind = item.get("구분", "")
    if kind == "상가":
        return ev["law_name"].startswith("주택")
    if kind == "주택":
        return ev["law_name"].startswith("상가")
    return False


def _search(store, question: str, k: int, where: dict | None, search_type: str):
    if search_type == "mmr":  # 후보를 넉넉히 가져온 뒤 서로 겹치지 않게 고른다 (F2014)
        return store.max_marginal_relevance_search(question, k=k, fetch_k=config.MMR_FETCH_K, filter=where)
    return store.similarity_search(question, k=k, filter=where)


def retrieve(question: str, item: dict | None, prec_store, doc_store,
             top_k: int | None = None, search_type: str | None = None) -> list[dict]:
    top_k = top_k or config.TOP_K
    search_type = search_type or config.SEARCH_TYPE
    evidence: list[dict] = []

    # 1) 판례 메타데이터 (RAG 1)
    if item:
        got = prec_store.get(ids=[item["판례일련번호"]])
        if got["ids"]:
            from langchain_core.documents import Document

            doc = Document(page_content=got["documents"][0], metadata=got["metadatas"][0])
            evidence.append(_evidence(got["ids"][0], doc))
    else:  # 판례를 고르지 않은 일반 질문은 비슷한 판례 몇 건을 함께 본다
        for doc in prec_store.similarity_search(question, k=config.GENERAL_PRECEDENT_K):
            evidence.append(_evidence(doc.metadata.get("case_id", ""), doc))

    # 2) 외부 문서 (RAG 2)
    where = {"case_id": {"$in": [item["판례일련번호"], ""]}} if item else None
    if doc_store._collection.count():
        # 다른 법 조문을 빼고도 Top-k개가 남도록 넉넉히 찾은 뒤 자른다
        found = [_evidence(doc.id or f"{doc.metadata.get('doc_id')}:{doc.metadata.get('chunk')}", doc)
                 for doc in _search(doc_store, question, top_k * 2, where, search_type)]
        evidence.extend([ev for ev in found if not _other_law(ev, item)][:top_k])

    # 3) 병합·중복 제거
    seen_ids, seen_text, merged = set(), set(), []
    for ev in evidence:
        key_text = " ".join(ev["text"].split())
        if ev["id"] in seen_ids or key_text in seen_text:
            continue
        seen_ids.add(ev["id"])
        seen_text.add(key_text)
        merged.append(ev)
    return merged
