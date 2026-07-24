"""Regression tests for scripts/check_router_soc.py."""

from unittest.mock import patch

import pytest

import scripts.check_router_soc as check_router_soc


def test_find_violations_empty_when_clean() -> None:
    assert check_router_soc.find_violations() == {}


def test_pattern_matches_direct_db_calls() -> None:
    pattern = check_router_soc.VIOLATION_PATTERN
    assert pattern.search("x = db.query(Foo)")
    assert pattern.search("db.add(item)")
    assert pattern.search("db.commit()")
    assert pattern.search('db.execute(text("x"))')
    assert pattern.search("db.delete(item)")


def test_pattern_does_not_match_unrelated_names() -> None:
    pattern = check_router_soc.VIOLATION_PATTERN
    assert not pattern.search("foo_db.query(Bar)")
    assert not pattern.search("sub_db.commit()")
    assert not pattern.search("p4x_service.get_active_accounts(db)")


def test_find_violations_detects_bad_file(tmp_path) -> None:
    router_dir = tmp_path / "router_includes"
    router_dir.mkdir()
    (router_dir / "bad.py").write_text("def f():\n    return db.query(Foo).all()\n")
    (router_dir / "good.py").write_text("def f():\n    return service.get_foo(db)\n")

    with patch.object(check_router_soc, "ROUTER_DIR", router_dir):
        violations = check_router_soc.find_violations()

    assert list(violations.keys()) == [router_dir / "bad.py"]
    assert violations[router_dir / "bad.py"] == [(2, "return db.query(Foo).all()")]


def test_main_exits_zero_when_clean(capsys) -> None:
    with (
        patch.object(check_router_soc, "find_violations", return_value={}),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_router_soc.main()
    assert exc_info.value.code == 0
    assert "OK" in capsys.readouterr().out


def test_main_exits_one_with_violations(capsys) -> None:
    fake_path = check_router_soc.ROUTER_DIR / "bad.py"
    violations = {fake_path: [(5, "db.query(Foo)")]}
    with (
        patch.object(check_router_soc, "find_violations", return_value=violations),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_router_soc.main()
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "5: db.query(Foo)" in out
