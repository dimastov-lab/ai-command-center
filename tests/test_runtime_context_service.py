import pytest

from command_center.runtime import context_service


# --------------------------------------------------------------------------
# BANK/LEGAL sensitive-project gating
# --------------------------------------------------------------------------


@pytest.mark.parametrize("project_id", ["BANK", "LEGAL"])
def test_sensitive_project_never_includes_unconfirmed_content(project_id):
    bundle = context_service.assemble_context(
        project_id=project_id,
        metadata={"file_count": 3},
        candidate_content={"report:run1": "sensitive report body", "file:secret.md": "secret contents"},
        confirmed_items=None,
    )
    assert bundle["sensitive"] is True
    assert bundle["content"] == {}
    assert set(bundle["excluded_pending_confirmation"]) == {"report:run1", "file:secret.md"}


@pytest.mark.parametrize("project_id", ["BANK", "LEGAL"])
def test_sensitive_project_metadata_always_included(project_id):
    bundle = context_service.assemble_context(project_id=project_id, metadata={"file_count": 5})
    assert bundle["metadata"] == {"file_count": 5}


@pytest.mark.parametrize("project_id", ["BANK", "LEGAL"])
def test_sensitive_project_includes_only_explicitly_confirmed_items(project_id):
    bundle = context_service.assemble_context(
        project_id=project_id,
        candidate_content={"report:run1": "body A", "chat:conv1": "body B"},
        confirmed_items=["report:run1"],
    )
    assert bundle["content"] == {"report:run1": "body A"}
    assert bundle["excluded_pending_confirmation"] == ["chat:conv1"]


@pytest.mark.parametrize("project_id", ["BANK", "LEGAL"])
def test_sensitive_project_never_auto_attaches_diffs_prompts_or_chats(project_id):
    candidates = {
        "diff:run1": "diff body",
        "prompt:run1": "prompt body",
        "chat:conv1": "chat body",
        "document:file.pdf": "document body",
    }
    bundle = context_service.assemble_context(project_id=project_id, candidate_content=candidates, confirmed_items=[])
    assert bundle["content"] == {}
    assert set(bundle["excluded_pending_confirmation"]) == set(candidates)


@pytest.mark.parametrize("project_id", ["AIOS", "AICOS", "BUSINESS", "PERSONAL"])
def test_non_sensitive_project_includes_all_candidate_content_without_confirmation(project_id):
    bundle = context_service.assemble_context(
        project_id=project_id,
        candidate_content={"file:a.md": "a", "file:b.md": "b"},
        confirmed_items=None,
    )
    assert bundle["sensitive"] is False
    assert bundle["content"] == {"file:a.md": "a", "file:b.md": "b"}
    assert bundle["excluded_pending_confirmation"] == []


def test_confirming_an_item_for_one_sensitive_project_does_not_leak_to_another_key():
    bundle = context_service.assemble_context(
        project_id="BANK",
        candidate_content={"report:run1": "body", "report:run2": "other body"},
        confirmed_items=["report:run1"],
    )
    assert bundle["content"] == {"report:run1": "body"}
    assert "report:run2" not in bundle["content"]


def test_no_candidate_content_is_fine():
    bundle = context_service.assemble_context(project_id="BANK", metadata={"n": 1})
    assert bundle["content"] == {}
    assert bundle["excluded_pending_confirmation"] == []


# --------------------------------------------------------------------------
# Explicit confirmation requirement for launches (and cancellations)
# --------------------------------------------------------------------------


def test_require_launch_confirmation_raises_when_not_confirmed():
    with pytest.raises(context_service.ConfirmationRequiredError):
        context_service.require_launch_confirmation(False, what="Launch")


def test_require_launch_confirmation_passes_when_confirmed():
    context_service.require_launch_confirmation(True, what="Launch")  # must not raise


def test_require_launch_confirmation_error_message_mentions_what():
    with pytest.raises(context_service.ConfirmationRequiredError, match="Cancelling a run"):
        context_service.require_launch_confirmation(False, what="Cancelling a run")
