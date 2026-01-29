import json
import streamlit as st
import pandas as pd

from core.front import run_front_pipeline
from core.middle import run_middle_pipeline
from core.back import run_back_pipeline
from core.storage import Storage
from core.policy import default_constraints, default_policies

st.set_page_config(page_title="Agentic Network Operations (F–M–B) Demo", layout="wide")

st.title("Agentic Network Operations Demo — F–M–B (Smart Factory)")
st.caption("LLM은 결정을 내리지 않고, 제약 파라미터(임계값/상하한/벌점가중치)만 생성·갱신합니다. 최종 결정은 규칙/정책/최적화 로직.")

# Persistent state
if "constraints" not in st.session_state:
    st.session_state["constraints"] = default_constraints()
if "policies" not in st.session_state:
    st.session_state["policies"] = default_policies()
if "event_json" not in st.session_state:
    st.session_state["event_json"] = None
if "intent_json" not in st.session_state:
    st.session_state["intent_json"] = None
if "decision_json" not in st.session_state:
    st.session_state["decision_json"] = None

storage = Storage(data_dir="data")
storage.init()

tabs = st.tabs(["FRONT", "MIDDLE", "BACK"])

with tabs[0]:
    st.subheader("FRONT — Domain Pipeline (Smart Factory)")
    st.write("공정 센서 → 이상 탐지 → 고장 예측 → 작업자 가이드 → 표준 Event JSON")

    colA, colB = st.columns([1, 1])

    with colA:
        st.markdown("### Input (도메인 데이터)")
        src = st.radio("데이터 소스", ["샘플 CSV 사용", "업로드"], horizontal=True)
        if src == "샘플 CSV 사용":
            df = pd.read_csv("data/sample_sensors.csv")
        else:
            up = st.file_uploader("CSV 업로드", type=["csv"])
            df = pd.read_csv(up) if up else pd.read_csv("data/sample_sensors.csv")

        st.dataframe(df.head(30), use_container_width=True)

    with colB:
        st.markdown("### AI Modeling (Threshold 조정)")
        z_thresh = st.slider("Anomaly Z-score threshold", 1.5, 5.0, 3.0, 0.1)
        fail_prob_thresh = st.slider("Failure probability threshold", 0.10, 0.95, 0.60, 0.01)
        model_uncertainty_thresh = st.slider("Uncertainty threshold (std/prob)", 0.01, 0.30, 0.10, 0.01)

        st.markdown("### LLM Post-processing 옵션")
        use_rag = st.checkbox("RAG(로컬 KB) 사용", value=True)
        llm_mode = st.selectbox("LLM 모드", ["AUTO(키 있으면 사용, 없으면 fallback)", "FORCE_FALLBACK"], index=0)

    if st.button("Run FRONT Pipeline", type="primary"):
        front_out = run_front_pipeline(
            df=df,
            z_thresh=z_thresh,
            fail_prob_thresh=fail_prob_thresh,
            uncertainty_thresh=model_uncertainty_thresh,
            constraints=st.session_state["constraints"],
            use_rag=use_rag,
            llm_mode=llm_mode,
        )
        st.session_state["event_json"] = front_out["event_json"]

        st.success("FRONT 완료: 표준 Event JSON 생성 → MIDDLE로 전달 준비")

        st.markdown("### Step 1) Input JSON")
        st.json(front_out["step1_input_json"])

        st.markdown("### Step 2) Preprocessing JSON (단계별)")
        st.json(front_out["step2_preprocess_json"])

        st.markdown("### Step 3) AI Modeling JSON")
        st.json(front_out["step3_ai_json"])

        st.markdown("### Step 4) LLM Post-processing JSON (요약/근거/가이드)")
        st.json(front_out["step4_llm_json"])

        st.markdown("### Step 5) Output — Standard Event JSON")
        st.json(front_out["event_json"])

        storage.append_hot_event(front_out["event_json"], layer="FRONT")

    st.divider()
    st.markdown("### 현재 전달 대기 Event JSON")
    st.json(st.session_state["event_json"] or {"status": "no event yet"})


with tabs[1]:
    st.subheader("MIDDLE — Intent Builder (Domain-invariant)")
    st.write("Situation Builder → Policy Mapper → Intent Converter (LLM은 Constraint Update JSON만)")

    if st.session_state["event_json"] is None:
        st.info("FRONT에서 Event JSON을 먼저 생성하세요.")
    else:
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("### 입력 Event JSON (from FRONT)")
            st.json(st.session_state["event_json"])
        with col2:
            st.markdown("### 현재 Constraints (운영 제약 파라미터)")
            st.json(st.session_state["constraints"])

        llm_mode_mid = st.selectbox("MIDDLE LLM 모드", ["AUTO(키 있으면 사용, 없으면 fallback)", "FORCE_FALLBACK"], index=0)

        if st.button("Run MIDDLE Pipeline", type="primary"):
            mid_out = run_middle_pipeline(
                event_json=st.session_state["event_json"],
                constraints=st.session_state["constraints"],
                policies=st.session_state["policies"],
                llm_mode=llm_mode_mid,
            )

            # Apply constraint updates (LLM or fallback output)
            st.session_state["constraints"] = mid_out["constraints_after"]
            st.session_state["intent_json"] = mid_out["intent_json"]

            st.success("MIDDLE 완료: Constraint Update 적용 + Intent JSON 생성(결정은 아님)")

            st.markdown("### Situation JSON")
            st.json(mid_out["situation_json"])

            st.markdown("### Policy Map JSON")
            st.json(mid_out["policy_map_json"])

            st.markdown("### LLM Constraint Update JSON (구조화 출력, 실패 시 fallback)")
            st.json(mid_out["constraint_update_json"])

            st.markdown("### Intent JSON (네트워크 액션: slice/priority/routing/RIS 등)")
            st.json(mid_out["intent_json"])

            storage.append_hot_event(mid_out["intent_json"], layer="MIDDLE")
            storage.upsert_constraints(st.session_state["constraints"], note="Applied in MIDDLE")

        st.divider()
        st.markdown("### 현재 Intent JSON")
        st.json(st.session_state["intent_json"] or {"status": "no intent yet"})


with tabs[2]:
    st.subheader("BACK — Policy & Memory (Domain-invariant)")
    st.write("비용/정책 평가 + Selective Resource + Closed-loop 업데이트 + Hot(JSONL)/Cold(SQLite) 저장")

    if st.session_state["intent_json"] is None:
        st.info("MIDDLE에서 Intent JSON을 먼저 생성하세요.")
    else:
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("### 입력 Intent JSON (from MIDDLE)")
            st.json(st.session_state["intent_json"])
        with col2:
            st.markdown("### 현재 Constraints / Policies")
            st.json({"constraints": st.session_state["constraints"], "policies": st.session_state["policies"]})

        selective_mode = st.selectbox(
            "Selective Resource 전략",
            [
                "AUTO(불확실성↑ or SLA risk↑ 시 고비용 리소스 사용)",
                "CHEAP_ONLY(항상 저비용)",
                "EXPENSIVE_ONLY(항상 고비용)",
            ],
            index=0,
        )

        if st.button("Run BACK Pipeline", type="primary"):
            back_out = run_back_pipeline(
                intent_json=st.session_state["intent_json"],
                constraints=st.session_state["constraints"],
                policies=st.session_state["policies"],
                selective_mode=selective_mode,
            )
            st.session_state["decision_json"] = back_out["decision_json"]
            st.session_state["constraints"] = back_out["constraints_after"]

            st.success("BACK 완료: 정책/비용 평가 기반 최종 결정 + 메모리 저장 + Closed-loop 업데이트")

            st.markdown("### Cost/Performance Evaluation JSON")
            st.json(back_out["eval_json"])

            st.markdown("### Final Decision JSON (규칙/정책/최적화 로직 결과)")
            st.json(back_out["decision_json"])

            st.markdown("### Closed-loop Update JSON (LLM 없이도 동작)")
            st.json(back_out["closed_loop_json"])

            storage.append_hot_event(back_out["decision_json"], layer="BACK")
            storage.write_cold_record(
                event=st.session_state["event_json"],
                intent=st.session_state["intent_json"],
                decision=back_out["decision_json"],
                eval_json=back_out["eval_json"],
            )
            storage.upsert_constraints(st.session_state["constraints"], note="Updated in BACK")

        st.divider()
        st.markdown("### Hot DB (최근 이벤트 JSONL) 미리보기")
        st.code(storage.preview_hot(n_lines=30), language="json")

        st.markdown("### Cold DB(SQLite) 최근 레코드 5개")
        st.dataframe(storage.query_recent_records(limit=5), use_container_width=True)
