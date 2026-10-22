"""임대차 판례 RAG — Streamlit 화면.

실행 (law 폴더에서):
    streamlit run app.py

화면 구성
    사이드바   화면 선택, 판례 선택 (F2009), 색인 건수 (F2002), 검색 설정 (F2006, F2014)
    질의응답   면책 안내 (F2012), 선택한 판례 정보, 채팅 (F2009), 답변 근거 (F2008)
    판례 찾기  자연어 조건으로 판례 찾기, 카드를 누르면 그 판례의 대화로 이동 (F2011)
    요건 점검  쟁점의 요건을 법령·판례 근거와 함께 하나씩 점검 (F2010)

UI는 화면만 맡는다. 수집·검색·생성은 collect.py, retrieval.py, graph.py가 맡는다 (NF2005).
오류가 나도 화면에는 사용자용 안내만 보여 주고 키·프롬프트·오류 추적 정보는 보여 주지 않는다 (NF2004).
"""

import os

import streamlit as st

import config
from collect import collect
from data_prep import precedents
from data_prep.law_api import LawApiError
from graph import NOT_FOUND, build_graph, cited_sources
from indexing import get_store
from llm import MissingKeyError, get_chat_model
from services import ISSUES, check_requirements, find_precedents

GENERAL = "__general__"  # 판례를 고르지 않은 일반 질문
MODES = ["질의응답", "판례 찾기", "요건 점검"]
DISCLAIMER = (
    "이 서비스는 공개된 법령과 판례를 바탕으로 한 참고용 정보이며 법률 자문이 아닙니다. "
    "개별 사건의 결론은 사실관계와 적용 시점의 법령에 따라 달라질 수 있습니다. "
    "중요한 결정을 하기 전에 대한법률구조공단, 임대차분쟁조정위원회, 변호사 등 전문가에게 확인하세요."
)

st.set_page_config(page_title="임대차 판례 RAG", page_icon="⚖️", layout="wide")


# ---------------------------------------------------------------- 자원 준비
@st.cache_resource(show_spinner=False)
def get_resources():
    model = get_chat_model()
    prec_store = get_store(config.PRECEDENT_COLLECTION)
    doc_store = get_store(config.LEGAL_DOC_COLLECTION)
    return model, prec_store, doc_store, build_graph(model, prec_store, doc_store)


@st.cache_data(show_spinner=False)
def load_catalog() -> tuple[list[dict], dict[str, str]]:
    items = precedents.load_items()
    summaries = precedents.load_summaries()
    names = {it["판례일련번호"]: precedents.display_name(it, summaries.get(it["판례일련번호"])) for it in items}
    return items, names


def stop_with(message: str) -> None:
    st.error(message)
    st.stop()


def open_case(case_id: str) -> None:
    """판례 카드의 버튼 콜백. 위젯이 그려지기 전에 실행되므로 선택값을 바꿀 수 있다 (F2011 → F2009)."""
    st.session_state.issue_filter = "전체"
    st.session_state.case_choice = case_id
    st.session_state.mode = "질의응답"


# ---------------------------------------------------------------- 근거 표시 (F2008)
def render_sources(answer: str, evidence: list[dict]) -> None:
    """답변이 실제로 인용한 [근거 N]만 보여 준다. 인용이 없으면 그 사실을 안내한다."""
    used = cited_sources(answer, evidence)
    if not used:
        st.caption("ⓘ 답변에 인용된 근거가 없습니다.")
        return
    with st.expander(f"답변에 사용한 근거 {len(used)}건"):
        for number, ev in used:
            link = f" · [원문]({ev['url']})" if ev["url"] else ""
            st.markdown(f"**[근거 {number}]** `{ev['label']}` {ev['title']}{link}")


# ---------------------------------------------------------------- 시작 점검 (F2012)
st.title("⚖️ 임대차 판례 RAG")
st.caption("주택·상가 임대차 판례와 법령을 찾아 근거와 함께 답합니다.")
st.warning(DISCLAIMER, icon="⚠️")

try:
    model, prec_store, doc_store, rag = get_resources()
except MissingKeyError:
    stop_with("GMS_KEY가 설정되지 않았습니다. law 폴더의 .env 파일에 GMS_KEY를 넣고 앱을 다시 실행하세요.")
except Exception:
    stop_with("Vector DB나 모델을 준비하지 못했습니다. 터미널에서 python indexing.py count 로 상태를 확인하세요.")

try:
    items, names = load_catalog()
except FileNotFoundError:
    stop_with("판례 목록(data/items_selected.json)이 없습니다. 터미널에서 python check_precedents.py --reanalyze 를 실행하세요.")

prec_count = prec_store._collection.count()
doc_count = doc_store._collection.count()
if prec_count == 0:
    stop_with("판례 색인이 0건입니다. 터미널에서 python indexing.py precedents 를 실행한 뒤 새로고침하세요.")

# ---------------------------------------------------------------- 사이드바
if "chats" not in st.session_state:
    st.session_state.chats = {}  # 판례일련번호(또는 GENERAL) → 대화 기록. 판례마다 따로 둔다 (F2009)

with st.sidebar:
    mode = st.radio("화면", MODES, key="mode", horizontal=True)
    st.header("판례 선택")
    issue = st.selectbox("쟁점으로 좁히기", ["전체"] + ISSUES, key="issue_filter")
    options = [GENERAL] + [it["판례일련번호"] for it in items if issue == "전체" or issue in it["쟁점"]]
    if st.session_state.get("case_choice") not in options:
        st.session_state.case_choice = GENERAL
    case_id = st.selectbox(
        "질문할 판례", options, key="case_choice",
        format_func=lambda cid: "판례 없이 일반 질문" if cid == GENERAL else names[cid],
    )
    st.caption(f"판례 {len(options) - 1}건")

    st.divider()
    st.metric("판례 색인", f"{prec_count}건")
    st.metric("외부 문서 청크", f"{doc_count}개")
    if not os.environ.get("LAW_OC"):
        st.info("LAW_OC가 없어 새 법령 문서를 가져올 수 없습니다. 이미 색인된 문서로만 답합니다.")

    with st.expander("검색 설정"):
        top_k = st.slider("외부 문서 근거 수 (Top-k)", 2, 12, config.TOP_K)
        use_mmr = st.toggle("MMR 검색 (근거 다양성)", value=config.SEARCH_TYPE == "mmr")

    if st.button("이 판례의 대화 지우기", width="stretch"):
        st.session_state.chats[case_id] = []

item = None if case_id == GENERAL else next(it for it in items if it["판례일련번호"] == case_id)
messages = st.session_state.chats.setdefault(case_id, [])

# ---------------------------------------------------------------- 판례 찾기 (F2011)
if mode == "판례 찾기":
    st.subheader("조건으로 판례 찾기")
    st.caption("상황이나 조건을 자연어로 쓰면, 조건에 맞는 판례만 골라 보여 줍니다. 예) 상가 임차인인데 건물이 팔린 뒤 보증금을 못 받았어요")
    with st.form("find_form"):
        condition = st.text_area("조건", height=80)
        submitted = st.form_submit_button("판례 찾기")
    if submitted and condition.strip():
        with st.status("조건에 맞는 판례를 찾는 중...", expanded=False) as status:
            try:
                st.session_state.found = find_precedents(condition, prec_store, model)
                status.update(label=f"조건에 맞는 판례 {len(st.session_state.found)}건", state="complete")
            except Exception:
                st.session_state.found = []
                status.update(label="판례를 찾지 못했습니다", state="error")
    found = st.session_state.get("found")
    if found == []:
        st.info("조건에 맞는 판례를 찾지 못했습니다. 조건을 바꿔 보세요.")
    for card in found or []:
        with st.container(border=True):
            st.markdown(f"**{card['display_name']}**")
            st.caption(f"{card['kind']} · 쟁점: {card['issues'].replace('/', ', ')} · 맞는 이유: {card['reason']}")
            st.button("이 판례로 대화하기", key=f"open_{card['case_id']}", on_click=open_case, args=(card["case_id"],))
    st.stop()

# ---------------------------------------------------------------- 요건 점검 (F2010)
if mode == "요건 점검":
    st.subheader("요건 점검")
    st.caption("쟁점의 요건을 법령과 판례 근거로 하나씩 점검합니다. 결과를 예측하지 않습니다. "
               "왼쪽에서 판례를 고르면 그 판례도 근거로 씁니다.")
    with st.form("check_form"):
        c1, c2 = st.columns(2)
        check_issue = c1.selectbox("쟁점", ISSUES)
        kinds = ["주택", "상가"]
        default_kind = 1 if item and item["구분"] == "상가" else 0
        check_kind = c2.radio("임대차 종류", kinds, index=default_kind, horizontal=True)
        situation = st.text_area("내 상황", height=100,
                                 placeholder="예) 3월 2일에 이사하고 전입신고를 했고, 확정일자는 3월 10일에 받았어요.")
        run_check = st.form_submit_button("요건 점검하기")
    if run_check:
        with st.status("조문과 판례를 모아 요건을 점검하는 중...", expanded=False) as status:
            try:
                result, evidence = check_requirements(check_issue, check_kind, situation, item,
                                                      model, prec_store, doc_store)
                st.session_state.check = {"answer": result, "evidence": evidence}
                status.update(label=f"근거 {len(evidence)}건으로 점검했습니다", state="complete")
            except Exception:
                st.session_state.check = None
                status.update(label="점검하지 못했습니다", state="error")
                st.error("요건을 점검하지 못했습니다. 키와 네트워크를 확인한 뒤 다시 시도해 주세요.")
    check = st.session_state.get("check")
    if check:
        st.markdown(check["answer"])
        render_sources(check["answer"], check["evidence"])
    st.stop()

# ---------------------------------------------------------------- 선택한 판례 정보
if item:
    st.subheader(names[case_id])
    st.caption(f"{item['법원명']} · 선고 {item['선고일자']} · {item['구분']} · 쟁점: {', '.join(item['쟁점'])}"
               f" · [원문]({precedents.source_url(case_id)})")
    with st.expander("판시사항 · 판결요지"):
        st.markdown(f"**판시사항**\n\n{item['판시사항']}")
        st.markdown(f"**판결요지**\n\n{item['판결요지'] or '(판결요지 없음)'}")
else:
    st.subheader("일반 질문")
    st.caption("판례를 고르지 않으면 법령 조문과 비슷한 판례를 함께 찾아 답합니다. 왼쪽에서 판례를 고를 수도 있습니다.")

# ---------------------------------------------------------------- 대화 다시 그리기
for message in messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_sources(message["content"], message.get("evidence", []))  # 이전 답변의 근거도 다시 그린다

# ---------------------------------------------------------------- 질문 처리
placeholder = "이 판례에 대해 물어보세요" if item else "임대차 상황이나 궁금한 점을 물어보세요"
question = st.chat_input(placeholder)
if question:
    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        answer, evidence = "", []
        with st.status("관련 문서를 찾고 있습니다...", expanded=False) as status:
            try:  # 외부 문서 수집 (F2003 ~ F2005)
                report = collect(question, item, model, doc_store, progress=lambda m: status.update(label=m))
                if report["cached"]:
                    status.write("이미 수집한 질문이라 수집을 건너뛰었습니다.")
                if report["selected"]:
                    status.write("수집한 문서: " + " · ".join(report["selected"]))
            except LawApiError:
                status.write("법령 문서를 새로 가져오지 못했습니다. 이미 색인된 문서로 답합니다.")
            except Exception:
                status.write("문서 수집 중 문제가 생겼습니다. 이미 색인된 문서로 답합니다.")

            status.update(label="답변을 만드는 중...")
            try:
                state = rag.invoke({"question": question, "item": item, "history": history,
                                    "top_k": top_k, "search_type": "mmr" if use_mmr else "similarity"})
                answer, evidence = state["answer"], state["evidence"]
                status.update(label=f"근거 {len(evidence)}건을 찾아 답변했습니다", state="complete")
            except Exception:
                status.update(label="답변을 만들지 못했습니다", state="error")

        if not answer:
            st.error("답변을 만들지 못했습니다. 키와 네트워크를 확인한 뒤 다시 시도해 주세요.")
        else:
            if not evidence or answer == NOT_FOUND:
                st.info("관련 근거를 찾지 못했습니다. 질문을 바꾸거나 다른 판례를 골라 보세요.")
            st.markdown(answer)
            render_sources(answer, evidence)

    if answer:
        messages.append({"role": "assistant", "content": answer, "evidence": evidence})
    else:
        messages.pop()  # 실패한 질문은 기록에 남기지 않는다
