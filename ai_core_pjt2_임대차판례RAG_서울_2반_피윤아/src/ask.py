"""터미널 질의응답 (검증 4.3 확인용). 수집 → retrieve → generate 를 한 번에 실행한다.

실행 (law 폴더에서):
    python ask.py "묵시적 갱신 후 해지 통지는 언제 효력이 생기나요?"
    python ask.py "새 집주인이 보증금을 돌려줘야 하나요?" --case 619451
    python ask.py "..." --top-k 3 --all          # Top-k를 바꾸고 검색된 근거 전체를 본다
    python ask.py "..." --mmr                    # 유사도 대신 MMR 검색 (F2014)
    python ask.py --graph                        # 그래프 구조 출력 (retrieve, generate 두 노드)
"""

import argparse

import config
from collect import collect
from data_prep import precedents
from graph import build_graph, cited_sources
from indexing import get_store
from llm import get_chat_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--case", help="선택한 판례일련번호")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--mmr", action="store_true")
    parser.add_argument("--all", action="store_true", help="검색된 근거 전체 출력")
    parser.add_argument("--no-collect", action="store_true", help="수집 없이 이미 색인된 문서만 사용")
    parser.add_argument("--graph", action="store_true", help="그래프 구조만 출력")
    args = parser.parse_args()

    model = get_chat_model()
    prec_store = get_store(config.PRECEDENT_COLLECTION)
    doc_store = get_store(config.LEGAL_DOC_COLLECTION)
    app = build_graph(model, prec_store, doc_store)
    if args.graph:
        print(app.get_graph().draw_mermaid())
        return
    if not args.question:
        raise SystemExit("질문을 입력하세요.")

    item = None
    if args.case:
        item = next((it for it in precedents.load_items() if it["판례일련번호"] == args.case), None)
        if not item:
            raise SystemExit("items_selected.json에 없는 판례입니다.")
    if not args.no_collect:
        report = collect(args.question, item, model, doc_store)
        print(f"수집: 대상 {len(report['selected'])}건, 새로 색인 {report['new_docs']}건"
              + (" (이미 수집한 질문)" if report["cached"] else ""))

    state = app.invoke({
        "question": args.question, "item": item, "history": [],
        "top_k": args.top_k, "search_type": "mmr" if args.mmr else None,
    })
    print("\n[답변]\n" + state["answer"])

    used = cited_sources(state["answer"], state["evidence"])
    print("\n[답변에 사용한 근거]")
    if not used:
        print("  답변에 인용된 근거가 없습니다.")
    for n, ev in used:
        print(f"  [근거 {n}] ({ev['label']}) {ev['title']} · {ev['url']}")
    if args.all:
        print(f"\n[검색된 근거 전체] {len(state['evidence'])}건 (Top-k={args.top_k or config.TOP_K}, "
              f"{'mmr' if args.mmr else config.SEARCH_TYPE})")
        for n, ev in enumerate(state["evidence"], start=1):
            print(f"  {n}. ({ev['label']}) {ev['title']}  [{ev['id']}]")


if __name__ == "__main__":
    main()
