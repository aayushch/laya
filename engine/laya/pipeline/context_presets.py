"""Context association strictness presets.

Maps named presets (strict / balanced / lenient) to concrete threshold
bundles consumed by context_grouping.py and entity_resolution.py.
"""

PRESETS: dict[str, dict] = {
    "strict": {
        "confidence_threshold": 0.15,
        "auto_confirm_threshold": None,
        "centroid_threshold": 0.18,
        "cross_platform_required": True,
        "entity_ref_overlap_mode": "hard_gate",
        "always_llm": True,
    },
    "balanced": {
        "confidence_threshold": 0.22,
        "auto_confirm_threshold": 0.10,
        "centroid_threshold": 0.25,
        "cross_platform_required": False,
        "entity_ref_overlap_mode": "soft_boost",
        "always_llm": False,
    },
    "lenient": {
        "confidence_threshold": 0.35,
        "auto_confirm_threshold": 0.18,
        "centroid_threshold": 0.35,
        "cross_platform_required": False,
        "entity_ref_overlap_mode": "disabled",
        "always_llm": False,
    },
}


def resolve_context_config(sg_config: dict) -> dict:
    """Resolve smart_grouping config into effective thresholds.

    Named presets override any raw threshold values in settings.
    Custom mode (or unrecognized preset) falls back to raw values.
    """
    strictness = sg_config.get("strictness", "strict")
    if strictness in PRESETS:
        return PRESETS[strictness]
    return {
        "confidence_threshold": sg_config.get("confidence_threshold", 0.22),
        "auto_confirm_threshold": sg_config.get("auto_confirm_threshold", 0.12),
        "centroid_threshold": sg_config.get("centroid_threshold", 0.25),
        "cross_platform_required": sg_config.get("cross_platform_required", False),
        "entity_ref_overlap_mode": sg_config.get("entity_ref_overlap_mode", "disabled"),
        "always_llm": sg_config.get("always_llm", False),
    }


def get_strictness(sg_config: dict) -> str:
    """Return the current strictness name."""
    return sg_config.get("strictness", "strict")


def _entity_refs_overlap(refs_a: str, refs_b: str) -> bool:
    """Check if two entity_ref strings share any meaningful identifier.

    Two-pass strategy:
    1. Exact token match (case-insensitive).
    2. Substring fallback for tokens > 5 chars — catches reformatted
       identifiers (e.g., "PaymentService" in "payment-service-crash").
    """
    if not refs_a or not refs_b:
        return False
    tokens_a = {t.strip().lower() for t in refs_a.split(",") if len(t.strip()) > 2}
    tokens_b = {t.strip().lower() for t in refs_b.split(",") if len(t.strip()) > 2}

    if tokens_a & tokens_b:
        return True

    long_a = {t for t in tokens_a if len(t) > 5}
    long_b = {t for t in tokens_b if len(t) > 5}
    for ta in long_a:
        for tb in long_b:
            if ta in tb or tb in ta:
                return True
    return False
