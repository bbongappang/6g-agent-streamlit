import json
import time
import uuid
from datetime import datetime


def now_iso():
    return datetime.utcnow().isoformat() + "Z"


def make_id(prefix="evt"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def safe_json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def softplus(x):
    # 안정적 softplus
    import math
    if x > 30:
        return x
    return math.log1p(math.exp(x))


def sleep_ms(ms):
    time.sleep(ms / 1000.0)
