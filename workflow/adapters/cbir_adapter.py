"""
adapters/cbir_adapter.py   (REPLACES the old file)
CBIR Adapter for Member 1 (AI/Imaging Module) Integration.

Provides:
- get_similar_cases(case_id, k=10): Returns the Top-K similar cases contract.
- Local MOCK retrieval or live HTTP retrieval from Member 1's service (member1/cbir_server.py).
- Every response carries "source": "mock" | "live" | "mock-fallback".
  When the live call fails, results are mock, marked "mock-fallback", and each finding
  text starts with "[MOCK] " so nobody mistakes fake results for real ones on screen.
"""

import json
import logging
import random
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

from config import (
    USE_MOCK_CBIR,
    CBIR_BASE_URL,
    CBIR_DEFAULT_K,
    CBIR_MOCK_LATENCY_RANGE_MS,
    IMAGES_DIR,
)

logger = logging.getLogger("cbir_adapter")

MOCK_FINDINGS = [
    "No acute intracranial hemorrhage or mass effect detected.",
    "Bilateral lung fields clear. Cardiac silhouette within normal limits.",
    "Mild degenerative changes with minor foraminal narrowing.",
    "Right middle cerebral artery hyperdense sign indicative of early infarction.",
    "Subsegmental consolidation noted in the right lower lobe.",
    "Small baseline pleural effusion. No acute pneumothorax.",
    "Intact cruciate ligaments. Minor meniscus horizontal fraying.",
    "Non-displaced fracture of radial head with subtle intra-articular fat pad sign.",
    "Normal gray-white differentiation. No acute territorial ischemia.",
    "Stable cardiomegaly with mild pulmonary vascular congestion.",
]


def _get_mock_similar_cases(case_id: str, k: int = CBIR_DEFAULT_K) -> Dict[str, Any]:
    """Generates mock Top-K results (random findings, descending scores)."""
    min_ms, max_ms = CBIR_MOCK_LATENCY_RANGE_MS
    latency_ms = round(random.uniform(min_ms, max_ms), 1)
    time.sleep(latency_ms / 1000.0)

    available_images = list(IMAGES_DIR.glob("*.jpg")) + list(IMAGES_DIR.glob("*.png"))
    image_names = [p.stem for p in available_images if p.stem != case_id]

    results: List[Dict[str, Any]] = []
    base_score = 0.94
    for rank in range(1, k + 1):
        if image_names and len(image_names) >= rank:
            sim_case_id = image_names[rank - 1]
        else:
            sim_case_id = f"CASE-{random.randint(1, 100):04d}"

        score = round(max(0.60, base_score - (rank - 1) * 0.035 + random.uniform(-0.008, 0.008)), 3)
        reranked_score = round(min(0.99, score + random.uniform(0.015, 0.045)), 3)
        results.append({
            "rank": rank,
            "similar_case_id": sim_case_id,
            "score": score,
            "reranked_score": reranked_score,
            "thumbnail_path": f"static/images/{sim_case_id}.jpg",
            "finding": random.choice(MOCK_FINDINGS),
        })

    return {"case_id": case_id, "retrieval_ms": latency_ms, "results": results, "source": "mock"}


def _get_remote_similar_cases(case_id: str, k: int = CBIR_DEFAULT_K) -> Dict[str, Any]:
    """
    Calls Member 1's live service: GET {CBIR_BASE_URL}/retrieve/{case_id}?k={k}.
    On ANY failure (service down, 404, timeout, bad JSON) falls back to mock results,
    clearly marked as "mock-fallback".
    """
    url = f"{CBIR_BASE_URL.rstrip('/')}/retrieve/{case_id}?k={k}"
    t_start = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if "results" not in data:
            raise ValueError("response has no 'results' field")
        elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
        data["retrieval_ms"] = data.get("retrieval_ms", elapsed_ms)
        data["source"] = "live"
        return data
    except Exception as exc:
        logger.warning("Remote CBIR call to %s failed (%s). Using MOCK fallback.", url, exc)
        data = _get_mock_similar_cases(case_id, k=k)
        data["source"] = "mock-fallback"
        for item in data["results"]:
            item["finding"] = "[MOCK] " + item["finding"]
        return data


def get_similar_cases(case_id: str, k: int = CBIR_DEFAULT_K) -> Dict[str, Any]:
    """Public entry point. Switches between Mock and Live based on config.USE_MOCK_CBIR."""
    if USE_MOCK_CBIR:
        return _get_mock_similar_cases(case_id, k=k)
    return _get_remote_similar_cases(case_id, k=k)