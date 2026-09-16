from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_paper_timer_runs_at_nine_vietnam_and_retries_transient_failures():
    timer = (ROOT / "deploy/systemd/system-trading-paper.timer").read_text()
    service = (ROOT / "deploy/systemd/system-trading-paper.service.in").read_text()

    assert "OnCalendar=*-*-* 09:00:00 Asia/Ho_Chi_Minh" in timer
    assert "Persistent=true" in timer
    assert "Restart=on-failure" in service
    assert "RestartSec=10min" in service
    assert "StartLimitBurst=6" in service
    assert "python -m lab.paper_worker --once" in service


def test_makefile_exposes_reversible_user_timer_commands():
    makefile = (ROOT / "Makefile").read_text()

    assert "install-paper-timer:" in makefile
    assert "paper-timer-status:" in makefile
    assert "uninstall-paper-timer:" in makefile
