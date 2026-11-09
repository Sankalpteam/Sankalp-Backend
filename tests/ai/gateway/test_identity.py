"""The assistant's name, pinned where the answer actually comes from.

If someone asks "who are you", the reply is produced from the system instruction - not from
whatever the model saw during training. So the instruction is the thing worth a test: it has
to name the product, and it has to stop naming the one it replaced.
"""

from pathlib import Path

from app.ai.gateway.ai_rules import ANSWER_SYSTEM_INSTRUCTION, TOOL_SYSTEM_INSTRUCTION

VOICE_SOURCE = Path(__file__).resolve().parents[3] / "app" / "api" / "voice" / "vibe.py"


def test_the_answer_path_names_the_assistant() -> None:
    assert "You are Sankalp, the assistant in the Sankalp app." in ANSWER_SYSTEM_INSTRUCTION
    assert "if asked who you are, say Sankalp" in ANSWER_SYSTEM_INSTRUCTION


def test_the_tool_path_carries_the_same_identity() -> None:
    # A message that needs no tool is answered by the tool path too, so it must not be the
    # path that forgets who it is.
    assert ANSWER_SYSTEM_INSTRUCTION in TOOL_SYSTEM_INSTRUCTION


def test_no_instruction_still_names_the_old_product() -> None:
    for instruction in (ANSWER_SYSTEM_INSTRUCTION, TOOL_SYSTEM_INSTRUCTION):
        assert "Indoone" not in instruction


def test_the_voice_experience_is_sankalp_vibe() -> None:
    source = VOICE_SOURCE.read_text(encoding="utf-8")

    assert "called Sankalp Vibe in the Sankalp app" in source
    assert "Indoone" not in source


def test_the_identity_is_not_left_to_the_model() -> None:
    # The old instruction said "say Indoone AI". The new one names Sankalp and says nothing
    # about an AI suffix, which is the naming decision: the product is Sankalp.
    assert "Sankalp AI" not in ANSWER_SYSTEM_INSTRUCTION
