from __future__ import annotations

from xjobscore.skill_labels import MAX_LABEL_LENGTH, labels, skill_label
from xjobscore.skills import SKILLS_PATH, canon


def test_compact_labels_are_display_only_and_cover_the_reviewed_set() -> None:
    display = labels()

    assert len(display) == 173
    assert max(map(len, display.values())) <= MAX_LABEL_LENGTH
    # A label for an id the taxonomy no longer carries is dead weight that
    # renders nowhere and silently rots. The bare count above only notices that
    # *something* moved; this names what. `metal-shading-language` was removed
    # on 2026-08-26 after it left skills.yaml.
    assert set(display) <= set(canon()), sorted(set(display) - set(canon()))
    assert skill_label("artificial-intelligence", "unused") == "AI"
    assert skill_label("triton-kernel-language", "unused") == "Triton"
    assert skill_label("nvidia-triton-inference-server", "unused") == "Triton Server"
    assert skill_label("full-stack-development", "unused") == "Full Stack"
    assert skill_label("firmware-development", "unused") == "Firmware"
    assert skill_label("statistical-analysis", "unused") == "StatsAnalysis"
    assert skill_label("data-science", "unused") == "DataScience"
    assert skill_label("data-engineering", "unused") == "DataEng"
    assert skill_label("software-engineering", "unused") == "SWE"
    assert skill_label("operating-systems", "unused") == "OS"
    assert skill_label("computer-architecture", "unused") == "CompArch"
    assert skill_label("microservices-architecture", "unused") == "Microservice"
    assert skill_label("api-development", "unused") == "API-Dev"
    assert skill_label("api-design", "unused") == "API-Design"
    assert skill_label("database-design", "unused") == "DB-Design"
    assert skill_label("gpu-programming", "unused") == "GPU-Dev"
    assert skill_label("ai-safety", "unused") == "AI-Safety"
    assert skill_label("api-gateway", "unused") == "API-Gateway"
    assert skill_label("responsible-ai", "Responsible AI") == "Responsible AI"
    assert skill_label("python", "Python") == "Python"

    artificial_intelligence = canon()["artificial-intelligence"]
    assert artificial_intelligence.name == "Artificial intelligence (AI)"
    assert "Artificial intelligence" in artificial_intelligence.patterns
    assert SKILLS_PATH.name == "skills.yaml"


def test_projected_labels_are_unique() -> None:
    display = labels()
    projected = [display.get(skill.id, skill.name).casefold() for skill in canon().values()]
    assert len(projected) == len(set(projected))
