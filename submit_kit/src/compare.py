"""검증용 비교 실행 (F2015 RAG 전후 비교, F2014 유사도·MMR 비교).

실행 (law 폴더에서):
    python src/compare.py rag     # 대표 질문을 RAG 없이 한 번, RAG로 한 번 답하게 해서 results/compare_rag.md에 나란히 쓴다
    python src/compare.py mmr     # 같은 질문의 근거 목록을 유사도 검색과 MMR 검색으로 비교해 results/compare_mmr.md에 쓴다

결과 파일에는 답변과 근거 제목만 쓴다. API 키와 프롬프트 전문은 쓰지 않는다 (NF2004).
"""

import argparse
import time
from datetime import datetime

import config
from collect import collect
from graph import build_graph, cited_sources, tidy
from indexing import get_store
from llm import get_chat_model
from prompts import NO_RAG_PROMPT
from retrieval import retrieve

RESULTS_DIR = config.ROOT_DIR / "results"

# 근거 없이 답하면 틀리기 쉬운 질문 (검증 4.6). 기대 답은 현행 법령 조문 기준이다.
QUESTIONS = [
    {"q": "주택 임대차가 묵시적으로 갱신된 뒤 임차인이 계약 해지를 통지하면 언제 효력이 생기나요?",
     "expected": "임대인이 통지를 받은 날부터 3개월이 지나면 효력이 생긴다.",
     "basis": "주택임대차보호법 제6조의2 제2항"},
    {"q": "주택 임차인의 대항력은 언제부터 생기나요?",
     "expected": "주택의 인도와 주민등록을 마친 다음 날부터 제3자에게 효력이 생긴다.",
     "basis": "주택임대차보호법 제3조 제1항"},
    {"q": "주택 임차인은 계약갱신요구를 언제까지 해야 하나요?",
     "expected": "임대차기간이 끝나기 6개월 전부터 2개월 전까지 해야 한다.",
     "basis": "주택임대차보호법 제6조의3 제1항, 제6조 제1항"},
    {"q": "상가 임차인의 계약갱신요구권은 최초 임대차기간을 포함해 최대 몇 년까지 행사할 수 있나요?",
     "expected": "전체 임대차기간이 10년을 넘지 않는 범위에서 행사할 수 있다.",
     "basis": "상가건물 임대차보호법 제10조 제2항"},
    {"q": "주택 임대인이 계약 기간 중 차임이나 보증금 증액을 청구할 때 한도는 얼마인가요?",
     "expected": "약정한 차임이나 보증금의 20분의 1(5%)을 넘지 못한다.",
     "basis": "주택임대차보호법 제7조 제2항, 같은 법 시행령 제8조"},
    {"q": "임대차 계약서에 반려동물 금지 조항이 없는데 개를 키우면 계약 해지 사유가 되나요?",
     "expected": "수집한 법령·판례에 직접 다룬 근거가 없다. 확인되지 않는다고 답해야 한다.",
     "basis": "(문서에 없음: 모르는 것을 모른다고 답하는지 확인)"},
]

MMR_QUESTIONS = [
    "주택 임차인의 대항력은 언제부터 생기나요?",
    "상가 임차인의 계약갱신요구권은 최대 몇 년까지 행사할 수 있나요?",
]


def run_rag_compare() -> None:
    model = get_chat_model()
    prec_store = get_store(config.PRECEDENT_COLLECTION)
    doc_store = get_store(config.LEGAL_DOC_COLLECTION)
    app = build_graph(model, prec_store, doc_store)

    lines = [f"# RAG 사용 전후 답변 비교 (F2015)", "",
             f"- 실행: {datetime.now():%Y-%m-%d %H:%M} · 모델: {config.CHAT_MODEL} · Top-k={config.TOP_K}, {config.SEARCH_TYPE}",
             "- 비교 기준: 사실 정확성, 근거 제시 여부, 모르는 것을 모른다고 답하는지", ""]
    for n, case in enumerate(QUESTIONS, start=1):
        print(f"[{n}/{len(QUESTIONS)}] {case['q']}")
        no_rag = tidy(model.invoke(NO_RAG_PROMPT.format(question=case["q"])).content.strip())

        start = time.perf_counter()
        report = collect(case["q"], None, model, doc_store, progress=lambda *_: None)
        state = app.invoke({"question": case["q"], "item": None, "history": []})
        elapsed = time.perf_counter() - start
        used = cited_sources(state["answer"], state["evidence"])

        lines += [f"## Q{n}. {case['q']}", "",
                  f"- 기대 답: {case['expected']}", f"- 확인 조문: {case['basis']}", "",
                  "### RAG 없이", "", no_rag, "",
                  "### RAG 사용", "", state["answer"], "",
                  f"인용한 근거 {len(used)}건"
                  + (" (수집 캐시 사용)" if report["cached"] else f" (새로 색인 {report['new_docs']}건)")
                  + f" · 처리 시간 {elapsed:.1f}초", ""]
        lines += [f"- [근거 {i}] ({ev['label']}) {ev['title']}" for i, ev in used] or ["- 인용한 근거 없음"]
        lines += ["", "| 기준 | RAG 없이 | RAG 사용 |", "| --- | --- | --- |",
                  "| 사실 정확성 |  |  |", "| 근거 제시 |  |  |", "| 모르는 것을 모른다고 답함 |  |  |", ""]
    write("compare_rag.md", lines)


def run_mmr_compare() -> None:
    prec_store = get_store(config.PRECEDENT_COLLECTION)
    doc_store = get_store(config.LEGAL_DOC_COLLECTION)

    lines = ["# 유사도 검색과 MMR 검색 비교 (F2014)", "",
             f"- 실행: {datetime.now():%Y-%m-%d %H:%M} · Top-k={config.TOP_K} · MMR fetch_k={config.MMR_FETCH_K}",
             "- 같은 문서(같은 조문·같은 판결문)에서 나온 청크가 몇 개 겹치는지 함께 센다.", ""]
    for q in MMR_QUESTIONS:
        lines += [f"## {q}", ""]
        for search_type in ("similarity", "mmr"):
            evidence = retrieve(q, None, prec_store, doc_store, config.TOP_K, search_type)
            docs = [ev["id"].rsplit(":", 1)[0] for ev in evidence]  # 청크 ID에서 순번을 뺀 문서 단위
            lines += [f"### {search_type} (근거 {len(evidence)}건, 서로 다른 문서 {len(set(docs))}개)", ""]
            lines += [f"{i}. ({ev['label']}) {ev['title']}" for i, ev in enumerate(evidence, start=1)]
            lines.append("")
    write("compare_mmr.md", lines)


def write(name: str, lines: list[str]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / name
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {path.relative_to(config.ROOT_DIR)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=["rag", "mmr"])
    args = parser.parse_args()
    run_rag_compare() if args.what == "rag" else run_mmr_compare()


if __name__ == "__main__":
    main()
