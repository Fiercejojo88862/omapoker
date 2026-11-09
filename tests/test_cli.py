"""The front door: the Omarchy gate and the command line.

The gate is the one part of OmaPoker that runs on machines it does not like, so
it is checked here against invented releases rather than whatever this machine
happens to be. The command line is checked for the two things that matter: a
failed gate must never reach the interface, and `--check` must work without a
terminal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omapoker import cli, gate, paths

OMARCHY = """\
NAME="Omarchy"
ID=omarchy
ID_LIKE=arch
PRETTY_NAME="Omarchy"
BUILD_ID="4.0.4"
VERSION_ID="4.0.4"
"""

UBUNTU = """\
NAME="Ubuntu"
ID=ubuntu
ID_LIKE=debian
PRETTY_NAME="Ubuntu 24.04 LTS"
VERSION_ID="24.04"
"""

STRANGE_ARCH = """\
NAME="Some Arch Derivative"
ID=somearch
ID_LIKE="arch omarchy"
PRETTY_NAME="Some Arch Derivative"
"""


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    """No desktop notification, and never a write into the real state tree."""
    monkeypatch.setenv("OMAPOKER_NO_NOTIFY", "1")
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    return tmp_path


def release(tmp_path, text: str) -> Path:
    path = tmp_path / "os-release"
    path.write_text(text, encoding="utf-8")
    return path


def tree(tmp_path) -> Path:
    directory = tmp_path / "omarchy"
    directory.mkdir(exist_ok=True)
    return directory


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def test_a_real_omarchy_release_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/omarchy")
    result = gate.check_omarchy(release(tmp_path, OMARCHY), tree(tmp_path))
    assert result.ok, result.failures
    assert result.os_release_id == "omarchy"
    assert "4.0.4" in result.summary


def test_a_distro_that_says_id_like_omarchy_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/omarchy")
    install = tree(tmp_path)
    (install / "version").write_text("9.9.9", encoding="utf-8")
    result = gate.check_omarchy(release(tmp_path, STRANGE_ARCH), install)
    assert result.ok, result.failures
    assert result.build_id == "9.9.9"


def test_ubuntu_is_refused_with_reasons_and_a_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda name: None)
    result = gate.check_omarchy(release(tmp_path, UBUNTU), tree(tmp_path))
    assert not result.ok
    assert any("not an Omarchy system" in failure for failure in result.failures)
    assert any("'omarchy' command" in failure for failure in result.failures)

    notice = gate.render_refusal(result)
    assert "OmaPoker requires Omarchy" in notice
    assert "Ubuntu 24.04 LTS" in notice
    assert "https://omarchy.org/" in notice


def test_a_missing_install_tree_fails_even_on_omarchy(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/omarchy")
    result = gate.check_omarchy(release(tmp_path, OMARCHY), tmp_path / "nowhere")
    assert not result.ok
    assert any("install tree" in failure for failure in result.failures)


def test_an_unreadable_release_file_fails(tmp_path):
    result = gate.check_omarchy(tmp_path / "missing", tree(tmp_path))
    assert not result.ok
    assert "no os-release" in result.failures[0]


def test_a_refusal_is_logged_and_exits_three(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gate,
        "check_omarchy",
        lambda *a, **k: gate.GateResult(ok=False, failures=["invented for a test"]),
    )
    with pytest.raises(SystemExit) as exit_info:
        gate.gate_or_exit()
    assert exit_info.value.code == 3

    log = paths.LOG_DIR / "gate-refusals.log"
    assert log.is_file()
    assert "invented for a test" in log.read_text(encoding="utf-8")


def test_the_debug_flag_refuses_even_on_a_perfect_system(monkeypatch, capsys):
    monkeypatch.setattr(
        gate, "check_omarchy", lambda *a, **k: gate.GateResult(ok=True, pretty_name="Omarchy")
    )
    with pytest.raises(SystemExit) as exit_info:
        gate.gate_or_exit(force_refuse=True)
    assert exit_info.value.code == 3
    assert "OmaPoker requires Omarchy" in capsys.readouterr().err


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

def test_version_needs_no_gate(monkeypatch, capsys):
    monkeypatch.setattr(
        gate, "check_omarchy", lambda *a, **k: gate.GateResult(ok=False, failures=["x"])
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert "OmaPoker" in capsys.readouterr().out


def test_check_reports_the_system_and_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        gate,
        "check_omarchy",
        lambda *a, **k: gate.GateResult(ok=True, pretty_name="Omarchy", build_id="4.0.4"),
    )
    assert cli.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "gate is satisfied" in out
    assert "4.0.4" in out


def test_a_failed_gate_never_reaches_the_interface(monkeypatch, capsys):
    import omapoker.ui.app as ui

    class NeverBuilt:
        def __init__(self) -> None:  # pragma: no cover - must not happen
            pytest.fail("the interface was built despite a failed gate")

        def run(self) -> None:  # pragma: no cover - must not happen
            pytest.fail("the interface was run despite a failed gate")

    monkeypatch.setattr(ui, "OmaPokerApp", NeverBuilt)
    monkeypatch.setattr(
        gate,
        "check_omarchy",
        lambda *a, **k: gate.GateResult(ok=False, failures=["invented for a test"]),
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 3
    assert "Omarchy" in capsys.readouterr().err


def test_the_parser_documents_itself():
    help_text = cli.build_parser().format_help()
    assert "--check" in help_text
    assert "Omarchy" in help_text
    # The debug flag is hidden on purpose: it is for the author, not the player.
    assert "--pretend-not-omarchy" not in help_text


def test_python_dash_m_omapoker_is_the_console_script():
    import omapoker.__main__ as entry

    assert entry.main is cli.main


def test_the_bundled_content_survives_packaging():
    from omapoker.coach import load_content

    content = load_content(str(paths.content_dir()))
    assert content
