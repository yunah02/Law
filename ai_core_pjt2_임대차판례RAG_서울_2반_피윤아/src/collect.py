"""외부 문서 수집과 색인 (F2003 ~ F2005, NF2002).

흐름
    1. 프롬프트 1로 질문(과 선택한 판례)에서 조문·용어 검색어를 뽑는다.
    2. 검색어로 후보 문서를 모은다. 조문은 법령 본문에서, 해석례는 API 검색으로.
    3. 프롬프트 2로 후보 중 색인할 문서를 고른다. 선택한 판례의 판결문은 선별 없이 항상 넣는다.
    4. 고른 문서를 청킹해 legal_docs 컬렉션에 색인한다. 이미 색인한 문서는 건너뛴다.

실행 (law 폴더에서, 검증 4.2 확인용):
    python collect.py "묵시적 갱신 후 해지 통지는 언제 효력이 생기나요?"
    python collect.py "이 판례에서 양수인이 보증금을 돌려줘야 하나요?" --case 619451
"""

import argparse
import json
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config
from data_prep import law_api, precedents
from prompts import QUERY_PROMPT, SELECT_PROMPT

ARTICLE_REF = re.compile(r"(.+?)\s*(제\d+조(?:의\d+)?)")  # "제9조 제2항"처럼 항이 붙어도 조문까지만 읽는다


# ---------------------------------------------------------------- 1. 검색어 추출
def parse_json(text: str):
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    if not match:
        raise ValueError("JSON이 없습니다.")
    return json.loads(match.group(0))


def case_brief(item: dict | None) -> str:
    if not item:
        return "(선택한 판례 없음)"
    return f"{item['사건명']}\n판시사항: {item['판시사항'][:500]}\n참조조문: {item['참조조문']}"


def extract_queries(question: str, item: dict | None, model) -> dict:
    """프롬프트 1. 실패하면 질문 자체를 용어 검색어로 쓴다."""
    try:
        data = parse_json(model.invoke(QUERY_PROMPT.format(case=case_brief(item), question=question)).content)
        articles = [str(a).strip() for a in data.get("articles", [])][:6]
        keywords = [str(k).strip() for k in data.get("keywords", [])][:5]
    except Exception:
        articles, keywords = [], [question]
    return {"articles": articles, "keywords": keywords}


# ---------------------------------------------------------------- 2. 후보 모으기
def build_candidates(queries: dict, articles: list[dict]) -> list[dict]:
    """조문 지정 → 그 조문, 용어 → 제목·본문에 용어가 든 조문, 해석례 검색 결과."""
    by_ref = {(a["law_name"].replace(" ", ""), a["article_no"]): a for a in articles}
    picked: dict[str, dict] = {}
    for ref in queries["articles"]:
        m = ARTICLE_REF.match(ref)
        if m and (m.group(1).replace(" ", ""), m.group(2)) in by_ref:
            art = by_ref[(m.group(1).replace(" ", ""), m.group(2))]
            picked.setdefault(art["doc_id"], {**art, "query": ref})
    for kw in queries["keywords"]:
        words = kw.split()  # "해지 통지"처럼 띄어 쓴 용어는 낱말이 모두 들어 있으면 후보로 본다
        for art in articles:
            body = art["title"] + " " + art["text"]
            if words and all(w in body for w in words):
                picked.setdefault(art["doc_id"], {**art, "query": kw})
        for expc in law_api.search_expc(kw):
            picked.setdefault(expc["doc_id"], {**expc, "query": kw})
    return list(picked.values())[: config.MAX_CANDIDATES]


# ---------------------------------------------------------------- 3. 선별
def select(question: str, candidates: list[dict], model, item: dict | None = None, limit: int = 8) -> list[dict]:
    """프롬프트 2. 제목과 앞부분 80자만 보여 준다. 선택한 판례도 함께 보여 줘 주택/상가를 헷갈리지 않게 한다.
    실패하면 앞에서부터 limit개를 쓴다."""
    if not candidates:
        return []
    lines = "\n".join(
        f"{n}. {c['title']} — {c['text'][:80].replace(chr(10), ' ')}" for n, c in enumerate(candidates, start=1)
    )
    try:
        numbers = parse_json(model.invoke(SELECT_PROMPT.format(question=question, case=case_brief(item), candidates=lines, limit=limit)).content)
        chosen = [candidates[int(n) - 1] for n in numbers if 1 <= int(n) <= len(candidates)]
    except Exception:
        chosen = candidates[:limit]
    return chosen[:limit]


# ---------------------------------------------------------------- 4. 청킹
def to_chunks(doc: dict, item_id: str) -> list[Document]:
    """청크 ID = 항목 ID:문서 ID:순번 (F2005). 법령·해석례는 여러 판례가 함께 쓰므로 항목 ID를 common으로 둔다.
    청크마다 문서 제목을 앞에 붙여, 둘째 청크부터도 어느 조문인지 알 수 있게 한다."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP)
    issues = law_api.article_issues(doc["law_name"], doc["article_no"]) if doc["doc_type"] == "법령" else []
    meta = {
        "source_type": doc["doc_type"],
        "doc_type": doc["doc_type"],
        "doc_id": doc["doc_id"],
        "title": doc["title"],
        "law_name": doc.get("law_name", ""),
        "article_no": doc.get("article_no", ""),
        "effective_date": doc.get("effective_date", ""),
        "case_id": doc.get("case_id", ""),
        "issues": "/".join(issues),
        "source_url": doc.get("source_url", ""),
        "query": doc.get("query", ""),
        "collected_at": law_api.today(),
    }
    for issue in issues:  # Chroma는 부분 문자열 필터가 없어 쟁점마다 참/거짓 필드를 둔다 (3단계 검색 필터용)
        meta[f"issue_{issue}"] = True
    pieces = splitter.split_text(doc["text"]) or [doc["text"]]
    return [
        Document(id=f"{item_id}:{doc['doc_id']}:{n}", page_content=f"[{doc['title']}]\n{piece}",
                 metadata={**meta, "chunk": n})
        for n, piece in enumerate(pieces)
    ]


def index_docs(store, docs: list[dict], item_id_of) -> tuple[int, int, list[str]]:
    """(새로 넣은 문서 수, 새로 넣은 청크 수, 이미 있어 건너뛴 문서 제목). 이미 있는 문서는 다시 넣지 않는다 (NF2002)."""
    new_docs, new_chunks, skipped = 0, 0, []
    for doc in docs:
        item_id = item_id_of(doc)
        if store.get(ids=[f"{item_id}:{doc['doc_id']}:0"])["ids"]:
            skipped.append(doc["title"])
            continue
        chunks = to_chunks(doc, item_id)
        store.add_documents(chunks, ids=[c.id for c in chunks])
        new_docs += 1
        new_chunks += len(chunks)
    return new_docs, new_chunks, skipped


# ---------------------------------------------------------------- 수집 기록 (NF2002)
def load_collected() -> dict:
    if config.COLLECTED_JSON.exists():
        return json.loads(config.COLLECTED_JSON.read_text(encoding="utf-8"))
    return {}


def save_collected(log: dict) -> None:
    config.COLLECTED_JSON.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTED_JSON.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 전체 흐름
def question_key(question: str, item: dict | None) -> str:
    """같은 질문(띄어쓰기 차이 무시) + 같은 판례면 같은 키. LLM이 매번 검색어를 조금씩 다르게 뽑아도 재수집하지 않는다."""
    return f"{item['판례일련번호'] if item else 'common'}|{' '.join(question.split())}"


def collect(question: str, item: dict | None, model, store, progress=print) -> dict:
    """질문 1개에 대한 수집·색인. 결과 요약을 돌려준다 (화면 진행 상태와 검증 4.2에 쓴다)."""
    report = {"queries": {}, "candidates": [], "selected": [], "rejected": [], "skipped": [],
              "new_docs": 0, "new_chunks": 0, "cached": False}
    must: list[dict] = []
    if item:  # 선택한 판례 자체를 다룬 문서(판결문 전문)는 선별과 상관없이 항상 넣는다 (F2003, F2004)
        must.append({**law_api.precedent_fulltext(item), "query": "(선택한 판례)"})

    log = load_collected()
    key = question_key(question, item)
    if key in log:  # 이미 수집한 질문은 검색어 추출부터 건너뛴다 (NF2002, NF2001)
        report["cached"] = True
        report["queries"] = log[key].get("queries", {})
        selected = []
    else:
        progress("검색어를 정하는 중...")
        queries = extract_queries(question, item, model)
        report["queries"] = queries
        progress("법령 조문과 해석례 후보를 찾는 중...")
        candidates = build_candidates(queries, law_api.all_articles())
        progress(f"후보 {len(candidates)}건 중 필요한 문서를 고르는 중...")
        selected = select(question, candidates, model, item)
        chosen_ids = {c["doc_id"] for c in selected}
        report["candidates"] = [c["title"] for c in candidates]
        report["rejected"] = [c["title"] for c in candidates if c["doc_id"] not in chosen_ids]
        selected = [law_api.expc_body(c) if c["doc_type"] == "해석례" else c for c in selected]

    docs = must + selected
    report["selected"] = [d["title"] for d in docs]
    progress(f"문서 {len(docs)}건을 색인하는 중...")
    report["new_docs"], report["new_chunks"], report["skipped"] = index_docs(
        store, docs, lambda d: d.get("case_id") or "common"
    )
    if not report["cached"]:
        log[key] = {"question": question, "queries": report["queries"],
                    "docs": [d["doc_id"] for d in selected], "at": law_api.today()}
        save_collected(log)
    return report


def main() -> None:
    from indexing import get_store
    from llm import get_chat_model

    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--case", help="선택한 판례일련번호")
    args = parser.parse_args()

    item = None
    if args.case:
        item = next((it for it in precedents.load_items() if it["판례일련번호"] == args.case), None)
        if not item:
            raise SystemExit("items_selected.json에 없는 판례입니다.")
    store = get_store(config.LEGAL_DOC_COLLECTION)
    report = collect(args.question, item, get_chat_model(), store)

    print("\n[프롬프트 1] 검색어:", report["queries"])
    if report["cached"]:
        print("이미 수집한 질문입니다. 검색어 추출과 수집을 건너뛰었습니다. (NF2002)")
    print(f"[후보] {len(report['candidates'])}건")
    print("[색인 대상 문서]")
    for t in report["selected"]:
        mark = " (이미 색인되어 건너뜀)" if t in report["skipped"] else " (새로 색인)"
        print("  +", t + mark)
    print("[버린 문서]")
    for t in report["rejected"]:
        print("  -", t)
    print(f"새로 색인: 문서 {report['new_docs']}건, 청크 {report['new_chunks']}개")
    print(f"legal_docs 컬렉션 전체: {store._collection.count()}개")


if __name__ == "__main__":
    main()
