"""Unit tests for ToolReplacer and ToolGroupReplacer output format.

Verifies that after the sandbox-native-tool-call refactor, the replacers
produce ``[Tool: name]`` instead of ``[Executable: name_uuid --help command]``.
"""

from core.skill.assembler.replacers import ToolGroupReplacer, ToolReplacer
from core.skill.entities.skill_metadata import SkillMetadata, ToolReference
from core.tools.entities.tool_entities import ToolProviderType


def _make_metadata(tools: list[ToolReference]) -> SkillMetadata:
    return SkillMetadata(tools={ref.uuid: ref for ref in tools})


def _make_tool_ref(uuid: str, name: str, enabled: bool = True) -> ToolReference:
    return ToolReference(
        uuid=uuid,
        type=ToolProviderType.MCP,
        provider="test_provider",
        tool_name=name,
        enabled=enabled,
    )


# ── ToolReplacer tests ────────────────────────────────────────────


class TestToolReplacer:
    """Tests for individual tool placeholder replacement."""

    def test_normal_tool_produces_tool_name(self) -> None:
        """R1: §[tool]...[uuid]§ → [Tool: tool_name]."""
        ref = _make_tool_ref("abc-123", "query_nearby_stores")
        metadata = _make_metadata([ref])

        replacer = ToolReplacer(metadata)
        content = "§[tool].[provider].[query_nearby_stores].[abc-123]§"
        result = replacer.resolve(content)

        assert result == "[Tool: query_nearby_stores]"

    def test_disabled_tool_returns_empty(self) -> None:
        """R2: Disabled tool reference → empty string."""
        ref = _make_tool_ref("abc-123", "disabled_tool", enabled=False)
        metadata = _make_metadata([ref])

        replacer = ToolReplacer(metadata)
        content = "§[tool].[provider].[disabled_tool].[abc-123]§"
        result = replacer.resolve(content)

        assert result == ""

    def test_not_found_tool_returns_error(self) -> None:
        """R3: Non-existent uuid → descriptive error."""
        metadata = _make_metadata([])

        replacer = ToolReplacer(metadata)
        content = "§[tool].[provider].[ghost].[missing-id]§"
        result = replacer.resolve(content)

        assert "Tool not found" in result
        assert "missing-id" in result

    def test_multiple_tools_in_content(self) -> None:
        """Multiple tool placeholders in one string are all resolved."""
        ref1 = _make_tool_ref("id-1", "tool_a")
        ref2 = _make_tool_ref("id-2", "tool_b")
        metadata = _make_metadata([ref1, ref2])

        replacer = ToolReplacer(metadata)
        content = (
            "Use §[tool].[p1].[tool_a].[id-1]§ and "
            "§[tool].[p2].[tool_b].[id-2]§ together."
        )
        result = replacer.resolve(content)

        assert "[Tool: tool_a]" in result
        assert "[Tool: tool_b]" in result
        assert "[Executable:" not in result


# ── ToolGroupReplacer tests ────────────────────────────────────────


class TestToolGroupReplacer:
    """Tests for grouped tool placeholder replacement."""

    def test_all_enabled_group(self) -> None:
        """R4: [§tool1§, §tool2§] → [Tool: n1, Tool: n2]."""
        ref1 = _make_tool_ref("id-1", "tool_a")
        ref2 = _make_tool_ref("id-2", "tool_b")
        metadata = _make_metadata([ref1, ref2])

        replacer = ToolGroupReplacer(metadata)
        content = (
            "[§[tool].[p1].[tool_a].[id-1]§, "
            "§[tool].[p2].[tool_b].[id-2]§]"
        )
        result = replacer.resolve(content)

        assert "[Tool: tool_a" in result
        assert "Tool: tool_b]" in result
        assert "[Executable:" not in result

    def test_all_disabled_group(self) -> None:
        """R5: All tools disabled → empty string."""
        ref1 = _make_tool_ref("id-1", "t1", enabled=False)
        ref2 = _make_tool_ref("id-2", "t2", enabled=False)
        metadata = _make_metadata([ref1, ref2])

        replacer = ToolGroupReplacer(metadata)
        content = (
            "[§[tool].[p1].[t1].[id-1]§, "
            "§[tool].[p2].[t2].[id-2]§]"
        )
        result = replacer.resolve(content)

        assert result == ""

    def test_mixed_enabled_group(self) -> None:
        """R6: Only enabled tools appear in the group output."""
        ref1 = _make_tool_ref("id-1", "enabled_tool")
        ref2 = _make_tool_ref("id-2", "disabled_tool", enabled=False)
        metadata = _make_metadata([ref1, ref2])

        replacer = ToolGroupReplacer(metadata)
        content = (
            "[§[tool].[p1].[enabled_tool].[id-1]§, "
            "§[tool].[p2].[disabled_tool].[id-2]§]"
        )
        result = replacer.resolve(content)

        assert "enabled_tool" in result
        assert "disabled_tool" not in result
        assert "[Executable:" not in result

    def test_no_executable_format_in_output(self) -> None:
        """Regression: output must never contain the old format."""
        ref = _make_tool_ref("uuid-1", "my_tool")
        metadata = _make_metadata([ref])

        group_replacer = ToolGroupReplacer(metadata)
        single_replacer = ToolReplacer(metadata)

        group_result = group_replacer.resolve(
            "[§[tool].[p].[my_tool].[uuid-1]§]"
        )
        single_result = single_replacer.resolve(
            "§[tool].[p].[my_tool].[uuid-1]§"
        )

        assert "Executable:" not in group_result
        assert "Executable:" not in single_result
