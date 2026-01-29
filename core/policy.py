def default_constraints():
    # "운영 제약"의 기본값(LLM이 업데이트할 대상)
    return {
        "sla": {
            "latency_max_ms": 20,
            "reliability_min": 0.999,
            "jitter_max_ms": 5,
        },
        "penalty": {
            "late_delivery": 3.0,
            "packet_loss": 5.0,
            "handover": 1.0,
            "power_cost": 1.5,
        },
        "thresholds": {
            "severity_block": 0.92,
            "urgency_high": 0.75,
            "uncertainty_high": 0.10,
        },
        "network": {
            "max_priority": 5,
            "default_slice": "mMTC",
            "safety_slice": "URLLC",
            "video_slice": "eMBB",
        },
    }


def default_policies():
    # 규칙/정책(결정 로직은 여기 + back의 평가로)
    return {
        "routing": {
            "safe_path": "PATH_A_LOW_LATENCY",
            "cheap_path": "PATH_C_LOW_COST",
            "balanced_path": "PATH_B_BALANCED",
        },
        "ris": {
            "passive_cost": 1.0,
            "active_cost": 7.0,
            "active_gain_reliability": 0.0007,
            "passive_gain_reliability": 0.0001,
        },
        "slice_profiles": {
            "URLLC": {"base_cost": 6.0, "latency_bias": -6, "reliability_bias": 0.0006},
            "eMBB": {"base_cost": 4.0, "latency_bias": -2, "reliability_bias": 0.0002},
            "mMTC": {"base_cost": 2.0, "latency_bias": +2, "reliability_bias": 0.0001},
        },
    }
