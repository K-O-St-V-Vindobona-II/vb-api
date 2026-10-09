"""Regression tests for scripts/check_router_soc.py."""

from unittest.mock import patch

import pytest

import scripts.check_router_soc as check_router_soc


def _violations_in(tmp_path, name: str, source: str):
    (tmp_path / name).write_text(source)
    with patch.object(check_router_soc, "API_DIR", tmp_path):
        return check_router_soc.find_violations()


def test_find_violations_empty_when_clean() -> None:
    assert check_router_soc.find_violations() == {}


@pytest.mark.parametrize(
    "line",
    [
        "x = db.query(Foo)",
        "db.add(item)",
        "db.add_all(items)",
        "db.commit()",
        'db.execute(text("x"))',
        "db.delete(item)",
        "member = db.get(Member, member_id)",
        "db.scalar(select(Foo))",
        "db.scalars(select(Foo))",
        "db.refresh(tx)",
        "db.flush()",
        "db.merge(item)",
    ],
)
def test_pattern_matches_direct_db_calls(line: str) -> None:
    assert check_router_soc.VIOLATION_PATTERN.search(line)


@pytest.mark.parametrize(
    "line",
    [
        "foo_db.query(Bar)",
        "sub_db.commit()",
        "p4x_service.get_active_accounts(db)",
        "settings.get('x')",
    ],
)
def test_pattern_does_not_match_unrelated_names(line: str) -> None:
    assert not check_router_soc.VIOLATION_PATTERN.search(line)


def test_find_violations_detects_bad_file(tmp_path) -> None:
    (tmp_path / "good.py").write_text("def f():\n    return service.get_foo(db)\n")

    violations = _violations_in(
        tmp_path, "bad.py", "def f():\n    return db.query(Foo).all()\n"
    )

    assert list(violations) == [tmp_path / "bad.py"]
    assert violations[tmp_path / "bad.py"] == [(2, "return db.query(Foo).all()")]


def test_find_violations_scans_subdirectories(tmp_path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()

    violations = _violations_in(nested, "deep.py", "db.commit()\n")

    assert list(violations) == [nested / "deep.py"]


@pytest.mark.parametrize(
    "source",
    [
        "from app.models.member import Member\n",
        "from app.models.p4x_fee import P4xFee\n",
        "    from app.models.contact import Contact\n",
        "import app.models.member\n",
    ],
)
def test_a_model_import_is_a_violation(tmp_path, source: str) -> None:
    violations = _violations_in(tmp_path, "router.py", source)

    assert list(violations) == [tmp_path / "router.py"]


@pytest.mark.parametrize(
    "source",
    [
        "from app.models.enums import AboutTabSlot\n",
        "from app.schemas.member import MemberResponse\n",
        "from app.services import member_service\n",
    ],
)
def test_enums_and_non_model_imports_are_allowed(tmp_path, source: str) -> None:
    assert _violations_in(tmp_path, "router.py", source) == {}


@pytest.mark.parametrize("name", ["deps.py", "auth_guards.py"])
def test_the_alias_modules_may_import_the_model(tmp_path, name: str) -> None:
    assert (
        _violations_in(tmp_path, name, "from app.models.member import Member\n") == {}
    )


def test_the_alias_modules_may_not_access_the_session(tmp_path) -> None:
    violations = _violations_in(tmp_path, "deps.py", "db.query(Member)\n")

    assert list(violations) == [tmp_path / "deps.py"]


def test_main_exits_zero_when_clean(capsys) -> None:
    with (
        patch.object(check_router_soc, "find_violations", return_value={}),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_router_soc.main()
    assert exc_info.value.code == 0
    assert "OK" in capsys.readouterr().out


def test_main_exits_one_with_violations(capsys) -> None:
    fake_path = check_router_soc.API_DIR / "router_includes" / "bad.py"
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
