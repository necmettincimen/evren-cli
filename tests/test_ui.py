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


def test_tool_args_skip_false_empty_and_schema_defaults():
    args = {
        "command": "pytest -q",
        "timeout_seconds": 180,      # schema default -> hidden
        "use_python": False,          # false -> hidden
        "use_powershell": False,
        "run_in_background": False,   # explicit schema default False -> hidden
        "alias": "prod",
        "empty": "",
    }
    summary = ui.format_tool_args("run_command", args)
    assert "command='pytest -q'" in summary
    assert "alias='prod'" in summary
    assert "timeout_seconds" not in summary
    assert "use_python" not in summary
    assert "run_in_background" not in summary
    assert "empty" not in summary


def test_tool_args_keep_non_default_values():
    summary = ui.format_tool_args(
        "run_command", {"command": "ls", "timeout_seconds": 30, "use_python": True}
    )
    assert "timeout_seconds=30" in summary
    assert "use_python=True" in summary


def test_tool_call_line_is_single_and_has_no_warning_mark(recording_console):
    ui.print_tool_call("run_command", {"command": "pytest", "use_python": False}, auto_approved=True)
    out = recording_console.file.getvalue()
    assert out.strip().count("\n") <= 1
    assert "⚡" in out
    assert "⚠" not in out
    assert "use_python" not in out


def test_markdown_stream_buffers_and_flushes_clean(recording_console):
    ui.print_markdown_stream("Merhaba **dünya**, `kod` var.")
    assert ui.markdown_stream_active()
    assert recording_console.file.getvalue() == ""  # nothing raw during stream
    ui.flush_markdown()
    out = recording_console.file.getvalue()
    assert "**" not in out
    assert "dünya" in out
    assert not ui.markdown_stream_active()
    ui.flush_markdown()  # empty flush is a no-op


def test_strip_markdown_plain_text_fallback():
    text = ui.strip_markdown("# Başlık\n**kalın** ve `kod` satırı\n- madde")
    assert "#" not in text
    assert "**" not in text
    assert "`" not in text
    assert "kalın" in text and "kod" in text and "madde" in text


def test_print_run_summary_single_line(recording_console):
    ui.print_run_summary(4, 12.34, 5678)
    out = recording_console.file.getvalue()
    assert "4 adım" in out and "12.3s" in out and "5678 token" in out
    assert out.strip().count("\n") == 0  # single line (rich adds a trailing newline)


def test_streamed_answer_renders_progressively_on_a_terminal(monkeypatch):
    """Rich + terminal: Live renders markdown as it streams; no raw ** left."""
    import io
    from rich.console import Console
    from src import ui

    fake = Console(file=io.StringIO(), force_terminal=True, width=60, record=True)
    monkeypatch.setattr(ui, "console", fake)
    monkeypatch.setattr(ui, "HAVE_RICH", True)
    for chunk in ["**Sonuç:** ", "testler ", "geçti.\n\n- madde"]:
        ui.print_markdown_stream(chunk)
        assert ui._STREAM["live"] is not None  # visible while streaming
    ui.flush_markdown()
    text = fake.export_text()
    assert "Sonuç:" in text and "testler geçti." in text
    assert "**" not in text
    assert ui._STREAM["live"] is None and not ui.markdown_stream_active()


def test_plain_mode_streams_line_by_line_without_marks(monkeypatch, capsys):
    from src import ui

    monkeypatch.setattr(ui, "HAVE_RICH", False)
    ui.print_markdown_stream("**Başlık** satırı\nikinci ")
    first = capsys.readouterr().out
    assert first == "Başlık satırı\n"          # complete line shown immediately
    ui.print_markdown_stream("`kod` parçası")
    ui.flush_markdown()
    assert capsys.readouterr().out == "ikinci kod parçası\n"
