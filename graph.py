"""RAG 파이프라인 (F2007). LangGraph를 retrieve와 generate 두 노드로 구성한다.

    START → retrieve → generate → END

- retrieve: 두 컬렉션에서 근거를 찾아 병합한다 (retrieval.py).
- generate: 프롬프트 3으로 근거만 사용해 답한다. 근거마다 번호와 출처 유형을 붙인다.

외부 문서 수집(collect.py)은 그래프 앞에서 한다. 화면에서 수집 진행 상태를 따로 보여 주기 위해서다.
"""

import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import config
from collect import case_brief
from prompts import ANSWER_PROMPT
from retrieval import retrieve

CITE_PATTERN = re.compile(r"\[근거[^\]]*\]")  # [근거 3], [근거 1, 3], [근거 4, 근거 3] 모두 읽는다
NOT_FOUND = "제공된 문서에서 확인되지 않습니다."
PRECEDENT_NOTE = "※ 인용한 판례는 선고 당시의 법령을 기준으로 한 판단입니다. 현재 법령과 다를 수 있습니다."


class RagState(TypedDict, total=False):
    question: str
    item: dict | None
    history: list[dict]      # [{"role": "user"|"assistant", "content": "..."}]
    top_k: int
    search_type: str
    evidence: list[dict]     # 번호 순서 = 리스트 순서 (1부터)
    answer: str


def format_context(evidence: list[dict]) -> str:
    """[근거 N] (출처 유형) 제목 형식. 출처 유형으로 판례 메타데이터와 외부 문서를 구분한다 (F2007)."""
    return "\n\n".join(
        f"[근거 {n}] ({ev['label']}) {ev['title']}\n{ev['text']}" for n, ev in enumerate(evidence, start=1)
    )


def format_history(history: list[dict]) -> str:
    recent = (history or [])[-config.HISTORY_TURNS:]
    if not recent:
        return "(없음)"
    return "\n".join(f"{'사용자' if h['role'] == 'user' else '답변'}: {h['content'][:300]}" for h in recent)


def cited_numbers(answer: str) -> set[int]:
    return {int(n) for tag in CITE_PATTERN.findall(answer) for n in re.findall(r"\d+", tag)}


def tidy(answer: str) -> str:
    """화면에서 깨지는 마크다운을 정리한다.
    - 굵게(**)는 한글 조사와 붙으면 표시되지 않고 별표가 그대로 남는다. 모두 지운다.
    - 제목(#)은 채팅 안에서 너무 크다. 일반 줄로 바꾼다."""
    answer = answer.replace("**", "")
    return re.sub(r"(?m)^\s*#{1,6}\s*", "", answer)


def cited_sources(answer: str, evidence: list[dict]) -> list[tuple[int, dict]]:
    """답변이 실제로 인용한 근거만 (번호, 근거)로 돌려준다. 인용이 없으면 빈 목록 (F2008)."""
    numbers = cited_numbers(answer)
    return [(n, ev) for n, ev in enumerate(evidence, start=1) if n in numbers]


def build_graph(model, prec_store, doc_store):
    def retrieve_node(state: RagState) -> dict:
        evidence = retrieve(state["question"], state.get("item"), prec_store, doc_store,
                            state.get("top_k"), state.get("search_type"))
        return {"evidence": evidence}

    def generate_node(state: RagState) -> dict:
        evidence = state.get("evidence", [])
        if not evidence:  # 근거가 없으면 LLM을 부르지 않는다. 추측할 여지를 없앤다
            return {"answer": NOT_FOUND}
        prompt = ANSWER_PROMPT.format(
            case=case_brief(state.get("item")), history=format_history(state.get("history", [])),
            context=format_context(evidence), question=state["question"],
        )
        answer = tidy(model.invoke(prompt).content.strip())
        # 판례를 인용했으면 선고 당시 법령 기준 안내를 반드시 붙인다. 프롬프트에만 맡기면 빠질 때가 있다
        if any(ev["source_type"] in ("판례", "판결문") for _, ev in cited_sources(answer, evidence)):
            answer += "\n\n" + PRECEDENT_NOTE
        return {"answer": answer}

    graph = StateGraph(RagState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
