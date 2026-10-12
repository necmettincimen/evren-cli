from io import StringIO

import pytest

from src import ui


LOG = "closing tag '[/controller/blob/controller.go:118]' and [bold]literal[/bold]"


@pytest.fixture
def recording_console(monkeypatch):
    if not ui.HAVE_RICH:
        pytest.skip("Rich is not installed")
    console = ui.Console(theme=ui.custom_theme, file=StringIO(), width=200, color_system=None)
    monkeypatch.setattr(ui, "console", console)
    return console


@pytest.mark.parametrize("printer", [ui.print_info, ui.print_success, ui.print_warning, ui.print_error])
def test_status_messages_print_untrusted_markup_literally(recording_console, printer):
    printer(LOG)
    assert LOG in recording_console.file.getvalue()


def test_tool_reasoning_and_command_output_print_markup_literally(recording_console):
    ui.print_tool_call("[run]", {"command": LOG})
    ui.print_tool_result("[run]", LOG)
    ui.print_tool_result("[run]", LOG, is_error=True)
    ui.print_reasoning(LOG, is_chunk=True)
    ui.print_command_output(LOG)
    ui.print_command_output(LOG, "stderr")
    output = recording_console.file.getvalue()
    assert "[run]" in output
    assert output.count("[/controller/blob/controller.go:118]") == 6
    assert output.count("[bold]literal[/bold]") == 6


def test_status_and_diff_titles_accept_markup_like_text(recording_console):
    with ui.status_spinner(LOG) as status:
        status.update("[/still/not/a/tag]")
    ui.print_diff("+changed", "[/path].py")
    assert "[/path].py" in recording_console.file.getvalue()
