import numpy as np
import pandas as pd

from core.utils import make_id, now_iso, sleep_ms
from core.rag import LocalRAG
from core.llm import get_constraint_update_json

def _preprocess(df: pd.DataFrame):
    steps = []
    df2 = df.copy()

    # Step A: 타입/결측
    steps.append({"name": "type_cast & missing_fill", "before_rows": int(df2.shape[0])})
    for c in ["temp_c", "vibration", "pressure_kpa", "rpm", "current_a"]:
        df2[c] = pd.to_numeric(df2[c], errors="coerce")
    df2 = df2.fillna(method="ffill").fillna(method="bfill")
    steps[-1]["after_rows"] = int(df2.shape[0])

    # Step B: rolling stats
    steps.append({"name": "rolling_features(window=5)", "features": []})
    for c in ["temp_c", "vibration", "pressure_kpa", "rpm", "current_a"]:
        df2[f"{c}_roll_mean"] = df2[c].rolling(5, min_periods=1).mean()
        df2[f"{c}_roll_std"] = df2[c].rolling(5, min_periods=1).std().fillna(0.0)
        steps[-1]["features"].append(f"{c}_roll_mean")
        steps[-1]["features"].append(f"{c}_roll_std")

    # Step C: z-score
    steps.append({"name": "zscore_features", "features": []})
    for c in ["temp_c", "vibration", "pressure_kpa", "rpm", "current_a"]:
        mu = df2[c].mean()
        sd = df2[c].std() + 1e-9
        df2[f"{c}_z"] = (df2[c] - mu) / sd
        steps[-1]["features"].append(f"{c}_z")

    return df2, steps


def _ai_model(df: pd.DataFrame, z_thresh: float, fail_prob_thresh: float):
    # Anomaly score: max abs z
    z_cols = [c for c in df.columns if c.endswith("_z")]
    df["anomaly_score"] = df[z_cols].abs().max(axis=1).clip(0, 10) / 10.0  # 0~1
    df["is_anomaly"] = (df[z_cols].abs().max(axis=1) >= z_thresh)

    # Failure probability: simple logistic on anomaly + vibration/current
    v = df["vibration"].values
    cur = df["current_a"].values
    a = df["anomaly_score"].values
    # normalize
    v_n = (v - v.mean()) / (v.std() + 1e-9)
    c_n = (cur - cur.mean()) / (cur.std() + 1e-9)
    logit = 1.2 * a + 0.7 * v_n / 3.0 + 0.5 * c_n / 3.0
    fail_prob = 1 / (1 + np.exp(-logit))
    df["failure_prob"] = np.clip(fail_prob, 0, 1)
    df["will_fail"] = df["failure_prob"] >= fail_prob_thresh

    # Uncertainty proxy: std of last 5 fail_prob
    df["failure_prob_std5"] = df["failure_prob"].rolling(5, min_periods=2).std().fillna(0.0)

    # Severity: combine fail_prob + anomaly
    df["severity"] = np.clip(0.55 * df["failure_prob"] + 0.45 * df["anomaly_score"], 0, 1)

    return df


def run_front_pipeline(
    df: pd.DataFrame,
    z_thresh: float,
    fail_prob_thresh: float,
    uncertainty_thresh: float,
    constraints: dict,
    use_rag: bool,
    llm_mode: str,
):
    # Step 1: Input
    step1_input_json = {
        "source": "smart_factory_sensors",
        "schema": list(df.columns),
        "rows": int(df.shape[0]),
        "sample": df.head(3).to_dict(orient="records"),
    }

    # Step 2: Preprocessing (단계별 진행 표시)
    df2, preprocess_steps = _preprocess(df)
    step2_preprocess_json = {
        "steps": preprocess_steps,
        "output_schema": list(df2.columns),
    }

    # UI용 "진행감" (데모 멈춤 방지)
    sleep_ms(150)

    # Step 3: AI Modeling
    df3 = _ai_model(df2, z_thresh=z_thresh, fail_prob_thresh=fail_prob_thresh)

    latest = df3.iloc[-1].to_dict()
    ai_json = {
        "anomaly_detection": {
            "z_thresh": z_thresh,
            "anomaly_score": float(latest["anomaly_score"]),
            "is_anomaly": bool(latest["is_anomaly"]),
        },
        "failure_prediction": {
            "fail_prob_thresh": fail_prob_thresh,
            "failure_prob": float(latest["failure_prob"]),
            "will_fail": bool(latest["will_fail"]),
        },
        "uncertainty": {
            "uncertainty_thresh": uncertainty_thresh,
            "failure_prob_std5": float(latest["failure_prob_std5"]),
            "is_uncertain": bool(float(latest["failure_prob_std5"]) >= uncertainty_thresh),
        },
        "severity": float(latest["severity"]),
        "machine_id": str(latest.get("machine_id", "M-01")),
        "process_line": str(latest.get("line", "L-1")),
        "timestamp": str(latest.get("timestamp", "")),
    }
    step3_ai_json = ai_json

    # Step 4: LLM Post-processing (요약/근거/가이드 / RAG 가능)
    kb = open("data/rag_kb.md", "r", encoding="utf-8").read()
    rag_hits = {"top_chunks": [], "scores": []}
    if use_rag:
        rag = LocalRAG(kb)
        q = f"스마트팩토리 고장 예측, 진동, 전류, 온도, 작업자 가이드, 안전"
        rr = rag.retrieve(q, k=3)
        rag_hits = {"top_chunks": rr.top_chunks, "scores": rr.scores}

    # FRONT의 LLM은 "요약/근거/가이드"만 (결정 X)
    summary = (
        f"기계 {ai_json['machine_id']} / 라인 {ai_json['process_line']}에서 "
        f"심각도 {ai_json['severity']:.2f}, 고장확률 {ai_json['failure_prediction']['failure_prob']:.2f}, "
        f"이상탐지 {ai_json['anomaly_detection']['is_anomaly']}."
    )
    guide = []
    if ai_json["severity"] > 0.8:
        guide += ["작업 즉시 감속/정지 검토", "베어링/모터 발열 점검", "진동 센서 체결/정렬 확인"]
    elif ai_json["failure_prediction"]["failure_prob"] > 0.6:
        guide += ["예방정비 티켓 생성", "윤활 상태 확인", "부하 분산 운전"]
    else:
        guide += ["모니터링 지속", "다음 점검 주기 유지"]

    llm_json = {
        "summary": summary,
        "evidence": {
            "anomaly_score": ai_json["anomaly_detection"]["anomaly_score"],
            "failure_prob": ai_json["failure_prediction"]["failure_prob"],
            "uncertainty_std5": ai_json["uncertainty"]["failure_prob_std5"],
        },
        "worker_guide": guide,
        "rag": rag_hits,
    }
    step4_llm_json = llm_json

    # Step 5: Output → 표준 Event JSON 생성 (MIDDLE로 전달)
    event_type = "maintenance_risk"
    if ai_json["severity"] >= constraints["thresholds"]["severity_block"]:
        event_type = "safety_risk"

    event_json = {
        "event_id": make_id("evt"),
        "ts": now_iso(),
        "domain": "smart_factory",
        "event_type": event_type,
        "entity": {
            "machine_id": ai_json["machine_id"],
            "line": ai_json["process_line"],
        },
        "metrics": {
            "severity": float(ai_json["severity"]),
            "anomaly_score": float(ai_json["anomaly_detection"]["anomaly_score"]),
            "failure_prob": float(ai_json["failure_prediction"]["failure_prob"]),
            "uncertainty": float(ai_json["uncertainty"]["failure_prob_std5"]),
        },
        "text": {
            "summary": llm_json["summary"],
            "worker_guide": llm_json["worker_guide"],
        },
        "provenance": {
            "front_pipeline": "sensors->anomaly->failure_pred->guide",
            "rag_used": bool(use_rag),
        },
    }

    return {
        "step1_input_json": step1_input_json,
        "step2_preprocess_json": step2_preprocess_json,
        "step3_ai_json": step3_ai_json,
        "step4_llm_json": step4_llm_json,
        "event_json": event_json,
    }
