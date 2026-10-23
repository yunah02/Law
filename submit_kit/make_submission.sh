#!/usr/bin/env bash
# 제출 폴더 만들기. law 폴더에서 실행한다.
#   bash submit_kit/make_submission.sh ../ai-pjt-02/ai_core_pjt2_주제명_지역_반_이름
set -e
T="$1"
if [ -z "$T" ]; then echo "사용법: bash submit_kit/make_submission.sh <제출 폴더 경로>"; exit 1; fi
if [ ! -f app.py ] || [ ! -d submit_kit ]; then echo "law 폴더에서 실행하세요."; exit 1; fi
KIT=submit_kit

mkdir -p "$T/src" "$T/data" "$T/docs" "$T/results" "$T/체크리스트"

# src: 소스 코드 (경로를 제출 폴더 기준으로 바꾼 config.py, compare.py는 키트 것을 쓴다)
cp *.py "$T/src/"
cp -r data_prep "$T/src/"
rm -rf "$T/src/data_prep/__pycache__"
cp requirements.txt "$T/src/"
cp "$KIT/src/config.py" "$KIT/src/compare.py" "$T/src/"
cp .env.example "$T/src/.env.sample"
[ -f .env ] && cp .env "$T/src/.env"          # 로컬 실행용. .gitignore로 Git에서 빠진다

# data: 수집한 데이터 (API 캐시 data/raw는 빼고)
cp data/*.json data/*.csv "$T/data/"

# 문서
cp -r docs/. "$T/docs/"
rm -f "$T/docs/rag_pipeline_design.md" "$T/docs/peer_checklist.md" "$T/docs/api.md"
cp "$KIT"/docs/*.md "$T/docs/"
cp -r results/. "$T/results/"
cp "$KIT/README.md" "$KIT/RAG_파이프라인_설계문서.md" "$KIT/.gitignore" "$T/"
cp "$KIT"/체크리스트/*.xlsx "$T/체크리스트/"

# 로컬 실행용 색인 (Git에서 빠진다)
[ -d chroma_db ] && cp -r chroma_db "$T/"

echo "완료: $T"
ls -a "$T"
