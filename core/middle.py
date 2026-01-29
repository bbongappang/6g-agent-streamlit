from copy import deepcopy
from core.llm import get_constraint_update_json
from core.utils import clamp

def _situation_builder(event_json: dict, constraints: dict):
    m = event_json["metrics"]
    severity = float(m.get("severity", 0.0))
    uncertainty = float(m.get("uncertainty", 0.0))

    # urgency: safety_risk > maintenance_risk, severity 가중
    urgency = 0.5 * severity
    if event_json.get("event_type") == "safety_risk":
        urgency += 0.4
    urgency = clamp(urgency, 0, 1)

    # sla risk: severity와 uncertainty로 근사
    sla_risk = clamp(0.7 * severity + 0.3 * (uncertainty / max(1e-6, constraints["thresholds"]["uncertainty_high"])), 0, 1)

    situation = {
        "severity": severity,
        "uncertainty": uncertainty,
        "urgency": urgency,
        "sla_risk": sla_risk,
        "event_type": event_json.get("event_type"),
        "domain": event_json.get("domain"),
        "entity": event_json.get("entity", {}),
    }
    return situation


def _policy_mapper(situation: dict, constraints: dict, policies: dict):
    sla_risk = situation["sla_risk"]
    urgency = situation["urgency"]
    uncertainty = situation["uncertainty"]

    # 기본 정책 템플릿 선택 (결정은 아님: intent 후보를 구성하는 매핑)
    if sla_risk > 0.8 or urgency > constraints["thresholds"]["urgency_high"]:
        profile = "URLLC"
        routing = policies["routing"]["safe_path"]
    elif uncertainty > constraints["thresholds"]["uncertainty_high"]:
        profile = "eMBB"   # 데이터 추가 수집/가시화
        routing = policies["routing"]["balanced_path"]
    else:
        profile = constraints["network"]["default_slice"]
        routing = policies["routing"]["cheap_path"]

    return {
        "selected_profile": profile,
        "routing_template": routing,
        "notes": "Policy mapping (not a decision): maps situation to candidate network posture."
    }


def _intent_converter(situation: dict, policy_map: dict, constraints_after: dict, policies: dict):
    # Intent는 네트워크 액션 구성 (slice/priority/routing/RIS 등)
    sev = situation["severity"]
    urg = situation["urgency"]
    unc = situation["uncertainty"]

    # priority: 1~max_priority (규칙 기반)
    maxp = int(constraints_after["network"]["max_priority"])
    priority = int(round(1 + (maxp - 1) * clamp(0.6 * urg + 0.4 * sev, 0, 1)))

    # slice 결정은 policy_map 사용 (여기서도 LLM X)
    slice_name = policy_map["selected_profile"]

    # RIS 요청 여부는 불확실성/위험 기반 "요청"으로만
    ris_request = "PASSIVE"
    if unc > constraints_after["thresholds"]["uncertainty_high"] or situation["sla_risk"] > 0.85:
        ris_request = "CONSIDER_ACTIVE"  # BACK에서 비용-성능 보고 최종 결정

    intent = {
        "intent_id": f"intent_{policy_map['selected_profile']}_{priority}",
        "ts": situation.get("ts"),
        "network_actions": {
            "slice": slice_name,
            "priority": priority,
            "routing": policy_map["routing_template"],
            "ris_mode": ris_request,
        },
        "constraints_snapshot": constraints_after,
        "situation": situation,
        "guardrails": {
            "llm_decision": False,
            "final_decision_in_back": True
        }
    }
    return intent


def run_middle_pipeline(event_json: dict, constraints: dict, policies: dict, llm_mode: str):
    # 1) Situation Builder
    situation_json = _situation_builder(event_json, constraints)

    # 2) LLM: Constraint Update JSON ONLY
    ctx = {"situation": situation_json, "constraints": constraints}
    constraint_update_json = get_constraint_update_json(ctx, mode=llm_mode)

    # 3) Apply updates to constraints (rule-based merge)
    constraints_after = deepcopy(constraints)
    for k in ["sla", "penalty", "thresholds"]:
        if k in constraint_update_json and isinstance(constraint_update_json[k], dict):
            constraints_after[k].update(constraint_update_json[k])

    # 4) Policy Mapper
    policy_map_json = _policy_mapper(situation_json, constraints_after, policies)

    # 5) Intent Converter (LLM X)
    intent_json = _intent_converter(situation_json, policy_map_json, constraints_after, policies)

    return {
        "situation_json": situation_json,
        "constraint_update_json": constraint_update_json,
        "constraints_after": constraints_after,
        "policy_map_json": policy_map_json,
        "intent_json": intent_json,
    }
