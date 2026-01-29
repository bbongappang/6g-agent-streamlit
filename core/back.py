from copy import deepcopy
from core.utils import clamp

def _evaluate(intent_json: dict, constraints: dict, policies: dict, selective_mode: str):
    s = intent_json["situation"]
    actions = intent_json["network_actions"]
    slice_name = actions["slice"]
    routing = actions["routing"]
    ris_req = actions.get("ris_mode", "PASSIVE")

    prof = policies["slice_profiles"][slice_name]

    # Baseline network performance model (단순 데모)
    latency = max(1, constraints["sla"]["latency_max_ms"] + prof["latency_bias"])
    reliability = constraints["sla"]["reliability_min"] + prof["reliability_bias"]
    cost = prof["base_cost"]

    # routing 영향 (cheap/balanced/safe)
    if "LOW_COST" in routing:
        cost -= 0.8
        latency += 3
        reliability -= 0.00015
    elif "LOW_LATENCY" in routing:
        cost += 1.2
        latency -= 4
        reliability += 0.0001
    else:
        cost += 0.3
        latency += 0
        reliability += 0.0

    # Selective RIS: 불확실성/위험 높을 때만 active 고려
    unc = float(s.get("uncertainty", 0.0))
    sla_risk = float(s.get("sla_risk", 0.0))

    use_active = False
    if selective_mode == "EXPENSIVE_ONLY":
        use_active = True
    elif selective_mode == "CHEAP_ONLY":
        use_active = False
    else:
        # AUTO
        if (unc > constraints["thresholds"]["uncertainty_high"]) or (sla_risk > 0.85) or (ris_req == "CONSIDER_ACTIVE"):
            use_active = True

    # RIS 적용 효과/비용
    if use_active:
        cost += policies["ris"]["active_cost"]
        reliability += policies["ris"]["active_gain_reliability"]
        latency -= 1
        ris_final = "ACTIVE"
    else:
        cost += policies["ris"]["passive_cost"]
        reliability += policies["ris"]["passive_gain_reliability"]
        ris_final = "PASSIVE"

    reliability = float(clamp(reliability, 0.0, 0.999999))
    latency = float(max(1.0, latency))

    # SLA violation penalties (결정은 back에서 최적화/규칙)
    p = constraints["penalty"]
    penalty = 0.0
    if latency > constraints["sla"]["latency_max_ms"]:
        penalty += p["late_delivery"] * (latency - constraints["sla"]["latency_max_ms"]) / 5.0
    if reliability < constraints["sla"]["reliability_min"]:
        penalty += p["packet_loss"] * (constraints["sla"]["reliability_min"] - reliability) / 0.0002

    # power cost weight
    penalty += p["power_cost"] * (cost / 10.0)

    objective = cost + penalty  # 최소화 대상 (데모)

    return {
        "predicted": {
            "latency_ms": latency,
            "reliability": reliability,
            "cost": cost,
            "ris_final": ris_final,
            "routing": routing,
            "slice": slice_name,
        },
        "penalty": penalty,
        "objective": objective,
        "notes": "Evaluation uses cost+penalty trade-off; final decision is policy/optimization, not LLM."
    }


def _final_decision(eval_json: dict, intent_json: dict, constraints: dict):
    pred = eval_json["predicted"]
    latency_ok = pred["latency_ms"] <= constraints["sla"]["latency_max_ms"]
    rel_ok = pred["reliability"] >= constraints["sla"]["reliability_min"]

    # 규칙/정책 기반 최종 결정
    decision = {
        "decision_id": "dec_" + intent_json["intent_id"],
        "apply": True,
        "network_config": {
            "slice": pred["slice"],
            "routing": pred["routing"],
            "ris_mode": pred["ris_final"],
            "priority": intent_json["network_actions"]["priority"],
        },
        "sla_check": {
            "latency_ok": latency_ok,
            "reliability_ok": rel_ok,
        },
        "objective": eval_json["objective"],
        "reason": [],
        "guardrails": {
            "llm_decision": False,
            "decision_logic": "rule/policy/optimization"
        }
    }

    if not latency_ok or not rel_ok:
        # degrade or escalate policy
        decision["reason"].append("Predicted SLA violation → escalate priority/routing if possible.")
        decision["network_config"]["priority"] = min(constraints["network"]["max_priority"], decision["network_config"]["priority"] + 1)
        if decision["network_config"]["ris_mode"] != "ACTIVE":
            decision["network_config"]["ris_mode"] = "ACTIVE"
            decision["reason"].append("Escalate RIS to ACTIVE to recover reliability.")
    else:
        decision["reason"].append("SLA satisfied under current trade-off.")

    return decision


def _closed_loop_update(constraints: dict, eval_json: dict):
    """
    Closed-loop (LLM 없이도 동작):
    - objective가 높거나 SLA 위반이면 threshold/penalty를 소폭 조정
    """
    c2 = deepcopy(constraints)
    obj = float(eval_json["objective"])
    lat = float(eval_json["predicted"]["latency_ms"])
    rel = float(eval_json["predicted"]["reliability"])

    violated = (lat > c2["sla"]["latency_max_ms"]) or (rel < c2["sla"]["reliability_min"])

    update = {"update_type": "closed_loop", "violated": violated, "actions": []}

    if violated:
        c2["penalty"]["packet_loss"] = min(30.0, c2["penalty"]["packet_loss"] * 1.05)
        c2["penalty"]["late_delivery"] = min(30.0, c2["penalty"]["late_delivery"] * 1.05)
        c2["thresholds"]["urgency_high"] = clamp(c2["thresholds"]["urgency_high"] - 0.01, 0.5, 0.9)
        update["actions"].append("Increase SLA violation penalties; lower urgency_high slightly to react earlier.")
    else:
        if obj > 12.0:
            c2["penalty"]["power_cost"] = min(5.0, c2["penalty"]["power_cost"] * 1.03)
            update["actions"].append("Objective high → slightly increase power_cost weight to discourage expensive configs.")
        else:
            update["actions"].append("Stable → keep constraints.")

    return c2, update


def run_back_pipeline(intent_json: dict, constraints: dict, policies: dict, selective_mode: str):
    eval_json = _evaluate(intent_json, constraints, policies, selective_mode)
    decision_json = _final_decision(eval_json, intent_json, constraints)
    constraints_after, closed_loop_json = _closed_loop_update(constraints, eval_json)

    return {
        "eval_json": eval_json,
        "decision_json": decision_json,
        "constraints_after": constraints_after,
        "closed_loop_json": closed_loop_json,
    }
