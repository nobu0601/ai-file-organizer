import os

import pytest

from conftest import approve_all, write
from history.db import F_REVIEW
from safety.rules import SafetyChecker, safe_component
from settings.config import Settings


@pytest.fixture
def checker(dirs):
    return SafetyChecker(Settings(organize_root=str(dirs["dest"])), protected_roots=[dirs["data"], dirs["dest"]])


@pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem", "vm.vmdk", "app.sqlite3", "yarn.lock",
                                  "credentials.json", "a.crdownload", "~$book.xlsx", "setup.exe"])
def test_forbidden_files(checker, dirs, name):
    p = write(dirs["watch"] / name, "x")
    assert not checker.check_source(p, dirs["watch"]).allowed


@pytest.mark.parametrize("folder", [".git", ".venv", "venv", "node_modules", "__pycache__"])
def test_forbidden_folders(checker, dirs, folder):
    p = write(dirs["watch"] / folder / "x.txt", "x")
    assert not checker.check_source(p, dirs["watch"]).allowed
    assert checker.is_dir_excluded(p.parent)


def test_symlink_not_followed(checker, dirs, tmp_path):
    target = write(tmp_path / "outside.txt", "x")
    link = dirs["watch"] / "link.txt"
    os.symlink(target, link)
    assert not checker.check_source(link, dirs["watch"]).allowed


def test_protected_roots(checker, dirs):
    assert not checker.check_source(write(dirs["dest"] / "a.txt", "x"), None).allowed
    assert not checker.check_source(write(dirs["data"] / "a.txt", "x"), None).allowed


def test_destination_validation(checker, dirs, tmp_path):
    assert checker.check_destination(dirs["dest"] / "cat" / "a.txt", dirs["dest"]).allowed
    assert not checker.check_destination(tmp_path / "elsewhere" / "a.txt", dirs["dest"]).allowed
    assert not checker.check_destination(dirs["dest"] / ".." / "a.txt", dirs["dest"]).allowed
    write(dirs["dest"] / "cat" / "b.txt", "x")
    assert not checker.check_destination(dirs["dest"] / "cat" / "b.txt", dirs["dest"]).allowed


def test_safe_component():
    assert safe_component("CON") == "_CON"
    assert safe_component("a:b*c?") == "a_b_c_"
    assert safe_component("名前. ") == "名前"


def test_git_repository_files_need_review(make_engine, sample_files, dirs):
    e = make_engine(recursive=True)
    e.propose_taxonomy()
    approve_all(e)
    repo = dirs["watch"] / "myrepo"
    (repo / ".git").mkdir(parents=True)
    f = write(repo / "alpha_試験結果_9.csv", "Alpha モーター 試験 結果 温度 基板")
    assert e.should_track(str(f)) is True
    out = e.process_file(f, "event")
    assert out.status == F_REVIEW and "開発プロジェクト" in out.message
    # .git の中身は追跡すらしない
    assert e.should_track(str(write(repo / ".git" / "config", "x"))) is False
