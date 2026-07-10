"""Тесты формата (не смысла) для CandidateMemoryCreateTool и для promote_candidate()."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.errors import SienaToolError  # noqa: E402
from logging_.logger import SienaLogger  # noqa: E402
from memory.candidate_memory_store import CandidateMemoryStore  # noqa: E402
from memory.long_memory_store import LongMemoryStore  # noqa: E402
from tools.candidate_memory_tools import CandidateMemoryCreateTool, promote_candidate  # noqa: E402

VALID_ARGS = dict(
    observation="Пользователь сказал, что переписывает память",
    insight="Это архитектурное решение проекта",
    reflection="Похоже на долгосрочное решение, а не мысль вслух",
    proposed_memory="Пользователь переписывает систему памяти Siena",
    confidence=0.9,
    category="decision",
)


@pytest.fixture
def tool(tmp_path):
    store = CandidateMemoryStore(tmp_path / "candidate_memory.sqlite3")
    logger = SienaLogger(tmp_path / "logs", "error")
    return CandidateMemoryCreateTool(store, logger), store


def test_valid_call_creates_pending_candidate(tool):
    candidate_tool, store = tool
    result = candidate_tool.run(**VALID_ARGS)
    assert result.ok
    assert result.content["status"] == "pending"
    assert store.get(result.content["id"]) is not None


@pytest.mark.parametrize("field", ["observation", "insight", "reflection", "proposed_memory"])
def test_empty_required_field_rejected(tool, field):
    candidate_tool, store = tool
    args = dict(VALID_ARGS)
    args[field] = "   "
    result = candidate_tool.run(**args)
    assert not result.ok
    assert store.list(limit=10) == []  # nothing written on validation failure


def test_non_string_required_field_rejected(tool):
    candidate_tool, _ = tool
    args = dict(VALID_ARGS)
    args["observation"] = 123
    result = candidate_tool.run(**args)
    assert not result.ok


def test_oversized_field_rejected(tool):
    candidate_tool, _ = tool
    args = dict(VALID_ARGS)
    args["proposed_memory"] = "x" * 2001
    result = candidate_tool.run(**args)
    assert not result.ok


@pytest.mark.parametrize("confidence", [-0.1, 1.5, "not-a-number"])
def test_confidence_out_of_range_or_bad_type_rejected(tool, confidence):
    candidate_tool, _ = tool
    args = dict(VALID_ARGS)
    args["confidence"] = confidence
    result = candidate_tool.run(**args)
    assert not result.ok


def test_blank_category_normalized_to_none(tool):
    candidate_tool, _ = tool
    args = dict(VALID_ARGS)
    args["category"] = "   "
    result = candidate_tool.run(**args)
    assert result.ok
    assert result.content["category"] is None


def test_promote_candidate_creates_long_memory_entry_and_marks_promoted(tmp_path):
    candidate_store = CandidateMemoryStore(tmp_path / "candidate_memory.sqlite3")
    long_store = LongMemoryStore(tmp_path / "long_memory.sqlite3")
    candidate = candidate_store.create(**VALID_ARGS)

    result = promote_candidate(candidate_store, long_store, candidate["id"])

    assert result["long_memory_entry"]["text"] == VALID_ARGS["proposed_memory"]
    assert candidate_store.get(candidate["id"])["status"] == "promoted"


def test_promote_candidate_twice_raises(tmp_path):
    candidate_store = CandidateMemoryStore(tmp_path / "candidate_memory.sqlite3")
    long_store = LongMemoryStore(tmp_path / "long_memory.sqlite3")
    candidate = candidate_store.create(**VALID_ARGS)

    promote_candidate(candidate_store, long_store, candidate["id"])
    with pytest.raises(SienaToolError):
        promote_candidate(candidate_store, long_store, candidate["id"])


def test_promote_candidate_missing_id_raises(tmp_path):
    candidate_store = CandidateMemoryStore(tmp_path / "candidate_memory.sqlite3")
    long_store = LongMemoryStore(tmp_path / "long_memory.sqlite3")
    with pytest.raises(SienaToolError):
        promote_candidate(candidate_store, long_store, 999)
