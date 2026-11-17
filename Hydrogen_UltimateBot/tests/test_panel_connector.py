import pytest

from cogs.panel_connector import (
    clean_panel_name,
    clean_panel_permission_names,
    parse_panel_action,
)


@pytest.mark.parametrize("raw, expected", [
    ('{"action":"restructure"}', "restructure"),
    ('```json\n{"action":"permissions"}\n```', "permissions"),
    ('Result: {"action":"busy_status"}', "busy_status"),
    ('{"action":"run_shell"}', "unsupported"),
    ('{"action":null}', "unsupported"),
    ('not json', "unsupported"),
    (None, "unsupported"),
])
def test_panel_action_parser_only_accepts_allowlisted_actions(raw, expected):
    assert parse_panel_action(raw) == expected


def test_panel_permission_filter_drops_unknown_and_non_string_values():
    assert clean_panel_permission_names([
        "view_channel", "send_messages", "administrator", 123, None,
        "view_channel",
    ]) == ["send_messages", "view_channel"]
    assert clean_panel_permission_names("send_messages") == []


def test_panel_name_is_normalized_and_bounded():
    assert clean_panel_name("General Chat!") == "general-chat"
    assert len(clean_panel_name("A" * 200)) <= 80
    assert clean_panel_name(123) == ""



@pytest.mark.parametrize("value", ["!!!", "---", "   ", "", None, 42])
def test_clean_panel_name_rejects_empty_normalized_values(value):
    assert clean_panel_name(value) == ""
