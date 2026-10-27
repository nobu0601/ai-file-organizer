"""混ざったフォルダを分ける（採用前の提案・採用後のフォルダ）。"""
from conftest import approve_all, write
from taxonomy.model import new_category_id

REPAIR = {
    "大規模修繕_見積比較.csv": "大規模修繕 見積 外壁 塗装 足場 修繕積立金 業者 比較",
    "大規模修繕_工程表.md": "大規模修繕 工程 外壁 足場 塗装 屋上防水 工期",
    "修繕積立金_推移.txt": "修繕積立金 大規模修繕 残高 推移 外壁 塗装 積立",
}
MEDICAL = {
    "医療費_集計_家族.csv": "医療費 病院 薬局 通院 診察 領収 家族 控除",
    "通院記録_2026.txt": "通院 病院 診察 薬局 医療費 予約 内科",
    "薬局_領収メモ.md": "薬局 医療費 処方 病院 領収 通院",
}


def _mixed(dirs):
    files = [write(dirs["watch"] / n, t) for n, t in {**REPAIR, **MEDICAL}.items()]
    return [str(f) for f in files]


def _add_mixed_proposal(e, files, name="大規模修繕と医療"):
    spec = {"category_id": new_category_id(), "category_name": name, "parent_category_id": None,
            "description": "", "classification_rules": {"keywords": ["大規模修繕", "医療費"], "keyword_weights": {}},
            "exclusion_rules": [], "examples": []}
    return e.db.add_proposal(kind="add", title=f"新しいフォルダ「{name}」", reason="テスト",
                             operations=[{"op": "add", "category": spec}], related_files=files), spec


def _by_theme(plan):
    """案の各まとまりが、修繕と医療のどちらか一方だけになっているか。"""
    themes = []
    for g in plan:
        names = {f.replace("\\", "/").split("/")[-1] for f in g["files"]}
        themes.append(("repair" if names <= set(REPAIR) else "medical" if names <= set(MEDICAL) else "mixed"))
    return themes


def test_plan_splits_mixed_proposal(make_engine, dirs):
    e = make_engine()
    files = _mixed(dirs)
    pid, _ = _add_mixed_proposal(e, files)
    plan = e.plan_split_proposal(pid)
    assert len(plan) == 2
    assert sorted(_by_theme(plan)) == ["medical", "repair"]
    assert sorted(f for g in plan for f in g["files"]) == sorted(files)   # すべてのファイルがどこかに入る
    assert all(g["name"] for g in plan)


def test_apply_split_proposal_then_approve(make_engine, dirs):
    e = make_engine(in_place=True)
    files = _mixed(dirs)
    pid, spec = _add_mixed_proposal(e, files)
    plan = e.plan_split_proposal(pid)
    # 画面で名前と行き先を直した想定: 1 件は入れない
    repair = next(g for g in plan if _by_theme([g]) == ["repair"])
    medical = next(g for g in plan if g is not repair)
    skipped = medical["files"][0]
    groups = [{"name": "大規模修繕", "files": repair["files"]},
              {"name": "医療費", "files": medical["files"][1:]},
              {"name": e.NOT_INCLUDED, "files": [skipped]}]
    ok, msg = e.apply_split_proposal(pid, groups)
    assert ok, msg
    pend = e.db.list_proposals("pending")
    assert sorted(p.operations[0]["category"]["category_name"] for p in pend) == ["医療費", "大規模修繕"]
    assert e.db.get_proposal(pid).status == "superseded"
    # 1 つ目は元のフォルダ ID を引き継ぐ
    assert spec["category_id"] in {p.operations[0]["category"]["category_id"] for p in pend}
    assert skipped not in {f for p in pend for f in p.related_files}
    approve_all(e)
    assert (dirs["watch"] / "大規模修繕").is_dir() and (dirs["watch"] / "医療費").is_dir()
    ready = {r.path: r.category_id for r in e.db.list_files(("ready",))}
    names = {c.category_id: c.category_name for c in e.store.taxonomy.active()}
    assert {names[ready[f]] for f in repair["files"]} == {"大規模修繕"}


def test_split_adopted_folder_moves_files_and_can_undo(make_engine, dirs):
    e = make_engine(in_place=True)
    files = _mixed(dirs)
    pid, _ = _add_mixed_proposal(e, files)
    approve_all(e)
    e.execute_ready()
    mixed = next(c for c in e.store.taxonomy.active() if c.category_name == "大規模修繕と医療")
    folder = dirs["watch"] / "大規模修繕と医療"
    assert len(list(folder.iterdir())) == 6
    plan = e.plan_split_category(mixed.category_id)
    assert sorted(_by_theme(plan)) == ["medical", "repair"]
    repair = next(g for g in plan if _by_theme([g]) == ["repair"])
    medical = next(g for g in plan if g is not repair)
    ok, msg = e.apply_split_category(mixed.category_id, [{"name": "大規模修繕", "files": repair["files"]},
                                                         {"name": "医療費", "files": medical["files"]}])
    assert ok, msg
    active = {c.category_name for c in e.store.taxonomy.active()}
    assert active == {"大規模修繕", "医療費"}          # 空になった元のフォルダは外れる
    assert not folder.exists()                          # 空のフォルダは片付く
    assert sorted(p.name for p in (dirs["watch"] / "大規模修繕").iterdir()) == sorted(REPAIR)
    assert sorted(p.name for p in (dirs["watch"] / "医療費").iterdir()) == sorted(MEDICAL)
    # 元に戻せる
    op = e.db.list_operations(1)[0]
    assert e.undo(op.id).ok


def test_split_adopted_folder_keeps_original_name(make_engine, dirs):
    e = make_engine(in_place=True)
    files = _mixed(dirs)
    _add_mixed_proposal(e, files)
    approve_all(e)
    e.execute_ready()
    mixed = next(c for c in e.store.taxonomy.active())
    plan = e.plan_split_category(mixed.category_id)
    repair = next(g for g in plan if _by_theme([g]) == ["repair"])
    medical = next(g for g in plan if g is not repair)
    ok, msg = e.apply_split_category(mixed.category_id, [{"name": mixed.category_name, "files": repair["files"]},
                                                         {"name": "医療費", "files": medical["files"]}])
    assert ok, msg
    assert e.store.taxonomy.get(mixed.category_id).active
    assert sorted(p.name for p in (dirs["watch"] / mixed.relative_path).iterdir()) == sorted(REPAIR)


def test_split_rejects_bad_groups(make_engine, dirs):
    e = make_engine()
    files = _mixed(dirs)
    pid, _ = _add_mixed_proposal(e, files)
    ok, msg = e.apply_split_proposal(pid, [{"name": "A", "files": files[:3]}, {"name": "a", "files": files[3:]}])
    assert not ok and "同じ名前" in msg
    ok, msg = e.apply_split_proposal(pid, [{"name": " ", "files": files}])
    assert not ok
    assert e.db.get_proposal(pid).status == "pending"
