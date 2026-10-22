"""Vector DB 색인 스크립트 (F2002, F2005).

실행 (law 폴더에서):
    python indexing.py precedents              # 일상어 요약 생성(캐시) + 판례 색인
    python indexing.py precedents --no-summary # 요약 없이 바로 색인 (빠른 확인용)
    python indexing.py count                   # 컬렉션별 청크 수 (검증 4.1)
    python indexing.py show 619451             # 판례 1건의 색인 텍스트 출력 (검증 4.1)
    python indexing.py search "집주인이 보증금을 안 돌려줘요"   # 판례 검색 맛보기

같은 판례를 다시 색인해도 ID(판례일련번호)가 같아 덮어쓰기만 일어난다.
"""

import argparse

from langchain_chroma import Chroma

import config
from data_prep import precedents
from llm import get_chat_model, get_embeddings


def get_store(name: str, embeddings=None) -> Chroma:
    return Chroma(
        collection_name=name,
        embedding_function=embeddings or get_embeddings(),
        persist_directory=str(config.CHROMA_DIR),
    )


def count(name: str, embeddings=None) -> int:
    return get_store(name, embeddings)._collection.count()


def index_precedents(use_summary: bool = True, embeddings=None, model=None) -> int:
    items = precedents.load_items()
    print(f"판례 로드: {len(items)}건")
    if use_summary:
        summaries = precedents.make_summaries(items, model or get_chat_model())
    else:
        summaries = precedents.load_summaries()
    docs = [precedents.to_document(it, summaries.get(it["판례일련번호"])) for it in items]
    store = get_store(config.PRECEDENT_COLLECTION, embeddings)
    store.add_documents(docs, ids=[d.id for d in docs])  # 같은 ID는 덮어쓴다 (upsert)
    total = store._collection.count()
    print(f"색인 완료: {config.PRECEDENT_COLLECTION} 컬렉션 {total}건 (로드 {len(items)}건과 같아야 함)")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("precedents")
    p.add_argument("--no-summary", action="store_true")
    sub.add_parser("count")
    s = sub.add_parser("show")
    s.add_argument("case_id")
    q = sub.add_parser("search")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=5)
    args = parser.parse_args()

    if args.cmd == "precedents":
        index_precedents(use_summary=not args.no_summary)
    elif args.cmd == "count":
        for name in (config.PRECEDENT_COLLECTION, config.LEGAL_DOC_COLLECTION):
            print(f"{name}: {count(name)}건")
    elif args.cmd == "show":
        got = get_store(config.PRECEDENT_COLLECTION).get(ids=[args.case_id])
        if not got["ids"]:
            print("색인에 없는 판례입니다.")
            return
        print(got["documents"][0])
        print("\n[메타데이터]")
        for key, value in got["metadatas"][0].items():
            print(f"- {key}: {value}")
    elif args.cmd == "search":
        hits = get_store(config.PRECEDENT_COLLECTION).similarity_search_with_score(args.query, k=args.k)
        for doc, score in hits:
            print(f"{score:.3f}  {doc.metadata['display_name']}")


if __name__ == "__main__":
    main()
