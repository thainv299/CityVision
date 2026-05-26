from __future__ import annotations
import os
import json
import time
from typing import Any, Dict

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, data: Dict[str, Any]) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clamp_int(x: float) -> int:
    return int(max(0, round(x)))