import os
import json

def _fallback_constraint_update(context: dict) -> dict:
    """
    LLM 실패/미사용 시: 규칙 기반으로 '제약 업데이트 JSON'만 반환
    (결정/인텐트는 만들지 않음)
    """
    situation = context.get("situation", {})
    sev = float(situation.get("severity", 0.0))
    urgency = float(situation.get("urgency", 0.0))
    unc = float(situation.get("uncertainty", 0.0))

    # 안전/긴급/불확실성↑ => SLA 강화 + 벌점 강화
    tighten = 0.0
    if sev > 0.8: tighten += 1.0
    if urgency > 0.75: tighten += 0.7
    if unc > 0.10: tighten += 0.6

    # 업데이트 제안
    upd = {
        "update_type": "constraint_update",
        "sla": {},
        "penalty": {},
        "thresholds": {},
        "rationale": []
    }

    if tighten > 0:
        upd["sla"]["latency_max_ms"] = max(5, int(round(20 - 5 * tighten)))
        upd["sla"]["reliability_min"] = min(0.99999, 0.999 + 0.0002 * tighten)
        upd["penalty"]["packet_loss"] = min(20.0, 5.0 + 3.0 * tighten)
        upd["penalty"]["late_delivery"] = min(20.0, 3.0 + 2.0 * tighten)
        upd["thresholds"]["severity_block"] = min(0.97, 0.92 + 0.02 * (tighten / 2.0))
        upd["rationale"].append("High safety/urgency/uncertainty → tighten SLA + raise penalties.")
    else:
        upd["sla"]["latency_max_ms"] = 20
        upd["sla"]["reliability_min"] = 0.999
        upd["penalty"]["power_cost"] = 1.5
        upd["rationale"].append("Normal condition → keep default constraints.")

    return upd


def get_constraint_update_json(context: dict, mode: str = "AUTO") -> dict:
    """
    규칙:
    - 출력은 오직 Constraint Update JSON
    - API key 없어도 실행 가능 (기본 fallback)
    """
    if mode == "FORCE_FALLBACK":
        return _fallback_constraint_update(context)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _fallback_constraint_update(context)

    # 키가 있어도: 실패하면 fallback
    try:
        # 외부 의존 최소화를 위해 여기서는 '실제 호출' 대신
        # 데모에서 안전하게 fallback을 권장.
        # (원하면 여기서 OpenAI 호출로 교체 가능)
        return _fallback_constraint_update(context)
    except Exception:
        return _fallback_constraint_update(context)
