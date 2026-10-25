"""Engineering cannot rewrite the frozen program or approve its own proposals."""

import datetime as dt
from pathlib import Path

import pytest

from autoresearch import engineering
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Program
from autoresearch.engineering import Edit, EngineeringProposal, stage, validate_edits
from autoresearch.ledger import Ledger
from autoresearch.workspace import digest_file, write_json
from llmrouter.contracts import ChatResponse

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    root = tmp_path / "session"
    for name in engineering.EDITABLE:
        path = root / "runtime" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def feature(x):\n    return x\n")
    write_json(root / engineering.POLICY_FILE, {})
    candidate = Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )
    program = Program(
        name="engineering-fixture",
        baseline=candidate,
        load_start=dt.date(2016, 1, 4),
        measured_start=dt.date(2018, 1, 2),
        train_end=dt.date(2022, 12, 30),
        validation_end=dt.date(2026, 8, 10),
        dependencies={},
        frozen_files={
            str(p.relative_to(root)): digest_file(p)
            for p in (root / "runtime").rglob("*")
            if p.is_file()
        },
    )
    monkeypatch.setattr(engineering, "load_program", lambda _: program)
    return root, program, candidate


def proposal(
    sources, path="autoresearch/features.py", replacement="def feature(x):\n    return x * 0.5\n"
):
    return EngineeringProposal(
        decision="propose",
        mechanism="Test attenuation of noisy signals.",
        falsification="Reject if matched net performance deteriorates.",
        edits=(
            Edit(
                path=path,
                before_sha256=sources["autoresearch/features.py"]["sha256"],
                replacement=replacement,
            ),
        ),
        required_tests=("Causal prefix invariance",),
    )


def test_stage_preserves_original_and_never_executes_code(frozen, tmp_path):
    root, program, _ = frozen
    sources = engineering.checked_sources(root, program)
    original = (root / "runtime/autoresearch/features.py").read_bytes()
    sentinel = tmp_path / "MUST_NOT_EXIST"
    generated = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    record = stage(root, proposal(sources, replacement=generated), sources, {"parent": "fixture"})
    assert not sentinel.exists()
    assert (root / "runtime/autoresearch/features.py").read_bytes() == original
    assert record["status"] == "awaiting_isolated_validation"
    assert record["promotion_authority"] == "none"
    assert "independent_tests" in record["unperformed_checks"]
    with pytest.raises(FileExistsError):
        stage(root, proposal(sources), sources, {})


@pytest.mark.parametrize(
    "path", ["../features.py", "/outside/features.py", "autoresearch/loop.py", "executor/broker.py"]
)
def test_protected_paths_rejected(frozen, path):
    root, program, _ = frozen
    sources = engineering.checked_sources(root, program)
    with pytest.raises(ValueError, match="protected"):
        validate_edits(proposal(sources, path=path), sources)


def test_hash_and_syntax_and_duplicate_edits_rejected(frozen):
    root, program, _ = frozen
    sources = engineering.checked_sources(root, program)
    good = proposal(sources)
    with pytest.raises(ValueError, match="digest"):
        validate_edits(
            good.model_copy(
                update={"edits": (good.edits[0].model_copy(update={"before_sha256": "a" * 64}),)}
            ),
            sources,
        )
    with pytest.raises(SyntaxError):
        validate_edits(proposal(sources, replacement="def incomplete("), sources)
    with pytest.raises(ValueError, match="duplicate"):
        validate_edits(good.model_copy(update={"edits": (good.edits[0], good.edits[0])}), sources)


def test_symlink_and_modified_source_rejected(frozen, tmp_path):
    root, program, _ = frozen
    path = root / "runtime/autoresearch/features.py"
    path.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        engineering.checked_sources(root, program)
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="frozen"):
        engineering.checked_sources(root, program)


def record_failures(ledger):
    with ledger.db:
        for i in range(3):
            ledger.db.execute(
                "INSERT INTO experiments(candidate,parent,proposal,status,error) VALUES(?,?,?,?,?)",
                (str(i), "parent", "{}", "crash", "not model context"),
            )


def test_automatic_handoff_is_luna_first_bounded_and_idempotent(frozen, monkeypatch):
    root, program, candidate = frozen
    calls = []

    class FakeRouter:
        def __init__(self, chain):
            assert [t.model for t in chain.for_task("coding")][:2] == [
                "gpt-5.6-luna",
                "gemini-3.8-flash",
            ]

        async def complete(self, request, ledger, *, validate, repair):
            calls.append(request)
            assert request.task_class == "coding" and not repair
            assert "not model context" not in request.user
            text = proposal(engineering.checked_sources(root, program)).model_dump_json()
            validate(text)
            return ChatResponse(
                provider="openai",
                model="gpt-5.6-luna",
                text=text,
                input_tokens=10,
                output_tokens=20,
            )

    monkeypatch.setattr(engineering, "Router", FakeRouter)
    with Ledger(root, 10000) as ledger:
        record_failures(ledger)
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=False)
            is None
        )
        result = engineering.automatic_handoff(
            root, program, ledger, candidate, models_enabled=True
        )
        assert result["status"] == "awaiting_isolated_validation"
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=True)
            is None
        )
        assert len(calls) == 1
        record_failures(ledger)
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=True)
            is None
        )
    with Ledger(root, 10000) as ledger:
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=True)
            is None
        )


def test_abstention_cannot_carry_edits(frozen):
    root, program, _ = frozen
    sources = engineering.checked_sources(root, program)
    with pytest.raises(ValueError, match="abstentions"):
        validate_edits(proposal(sources).model_copy(update={"decision": "abstain"}), sources)


def test_unavailable_provider_is_recorded_without_repeated_calls(frozen, monkeypatch):
    root, program, candidate = frozen
    calls = []

    class Unavailable:
        def __init__(self, chain):
            pass

        async def complete(self, *args, **kwargs):
            calls.append(1)

    monkeypatch.setattr(engineering, "Router", Unavailable)
    with Ledger(root, 10000) as ledger:
        record_failures(ledger)
        result = engineering.automatic_handoff(
            root, program, ledger, candidate, models_enabled=True
        )
        assert result["status"] == "provider_unavailable"
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=True)
            is None
        )
        assert len(calls) == 1


def test_improved_batch_does_not_request_code(frozen, monkeypatch):
    root, program, candidate = frozen
    monkeypatch.setattr(engineering, "Router", lambda _: pytest.fail("unnecessary coding call"))
    with Ledger(root, 10000) as ledger:
        record_failures(ledger)
        with ledger.db:
            ledger.db.execute("UPDATE experiments SET status='keep' WHERE id=3")
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=True)
            is None
        )


def test_interrupted_claim_stays_consumed(frozen, monkeypatch):
    root, program, candidate = frozen
    monkeypatch.setattr(engineering, "Router", lambda _: pytest.fail("must not retry"))
    with Ledger(root, 10000) as ledger:
        record_failures(ledger)
        ledger.put("engineering_attempts", ["claimed"])
        ledger.put("engineering", {"status": "requested", "request": "claimed"})
        assert (
            engineering.automatic_handoff(root, program, ledger, candidate, models_enabled=True)
            is None
        )
        assert ledger.get("engineering")["status"] == "interrupted"
