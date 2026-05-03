import uuid
import random
import string
from datetime import datetime


def generate_claim_number() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"CLM-{ts}-{suffix}"


def generate_id() -> str:
    return str(uuid.uuid4())


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    return max(min_val, min(max_val, value))


def truncate(text: str, max_len: int = 200) -> str:
    return text if len(text) <= max_len else text[:max_len] + "..."
