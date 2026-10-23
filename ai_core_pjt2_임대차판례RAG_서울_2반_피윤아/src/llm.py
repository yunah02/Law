"""LLM과 임베딩 모델을 만든다. 키는 환경변수(.env)에서만 읽는다 (NF2003)."""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

import config

load_dotenv()


class MissingKeyError(RuntimeError):
    """GMS_KEY가 없을 때. 화면에서는 이 오류를 잡아 안내 문구만 보여 준다 (F2012)."""


def _gms_key() -> str:
    key = os.environ.get("GMS_KEY", "").strip()
    if not key:
        raise MissingKeyError("GMS_KEY가 없습니다. .env를 확인하세요.")
    return key


def get_chat_model(temperature: float = 0) -> ChatOpenAI:
    return ChatOpenAI(
        model=config.CHAT_MODEL, api_key=_gms_key(), base_url=config.GMS_BASE_URL,
        temperature=temperature,
    )


def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=config.EMBED_MODEL, api_key=_gms_key(), base_url=config.GMS_BASE_URL)
