"""分類体系のデータモデルと変更操作。

正式な分類体系（承認済み）だけをここで扱う。AI の提案は history DB の proposals に
保存され、ユーザーが承認したときに apply_operations() で正式体系へ反映される。
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from safety.rules import safe_component


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_category_id() -> str:
    return "cat-" + uuid.uuid4().hex[:12]


@dataclass
class Category:
    category_id: str
    category_name: str
    parent_category_id: str | None = None
    relative_path: str = ""
    description: str = ""
    classification_rules: dict = field(default_factory=lambda: {"keywords": [], "keyword_weights": {}})
    exclusion_rules: list[str] = field(default_factory=list)
    approval_status: str = "approved"
    merged_into: str | None = None
    examples: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @property
    def active(self) -> bool:
        return self.approval_status == "approved"

    @property
    def keywords(self) -> list[str]:
        return list(self.classification_rules.get("keywords", []))

    @property
    def keyword_weights(self) -> dict[str, float]:
        w = dict(self.classification_rules.get("keyword_weights") or {})
        for k in self.keywords:
            w.setdefault(k, 1.0)
        return w

    @classmethod
    def from_dict(cls, d: dict) -> "Category":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: copy.deepcopy(v) for k, v in d.items() if k in known})


@dataclass
class Taxonomy:
    taxonomy_version: int = 0
    generated_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    categories: dict[str, Category] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "taxonomy_version": self.taxonomy_version,
            "generated_at": self.generated_at,
            "updated_at": self.updated_at,
            "categories": [asdict(c) for c in self.ordered()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Taxonomy":
        t = cls(
            taxonomy_version=int(d.get("taxonomy_version", 0)),
            generated_at=d.get("generated_at") or now_iso(),
            updated_at=d.get("updated_at") or now_iso(),
        )
        for c in d.get("categories", []):
            cat = Category.from_dict(c)
            t.categories[cat.category_id] = cat
        return t

    def clone(self) -> "Taxonomy":
        return Taxonomy.from_dict(copy.deepcopy(self.to_dict()))

    def active(self) -> list[Category]:
        return [c for c in self.ordered() if c.active]

    def get(self, cid: str | None) -> Category | None:
        return self.categories.get(cid) if cid else None

    def children(self, cid: str | None) -> list[Category]:
        return sorted(
            (c for c in self.categories.values() if c.parent_category_id == cid and c.active),
            key=lambda c: c.category_name,
        )

    def ordered(self) -> list[Category]:
        """親→子の深さ優先順。"""
        out: list[Category] = []
        seen: set[str] = set()

        def walk(pid: str | None) -> None:
            for c in sorted((c for c in self.categories.values() if c.parent_category_id == pid),
                            key=lambda c: (c.category_name, c.category_id)):
                if c.category_id in seen:
                    continue
                seen.add(c.category_id)
                out.append(c)
                walk(c.category_id)

        walk(None)
        # 親が見つからないもの（壊れたデータ）も末尾に含める
        out.extend(c for c in self.categories.values() if c.category_id not in seen)
        return out

    def depth(self, cid: str) -> int:
        d, c = 0, self.get(cid)
        while c and c.parent_category_id:
            d += 1
            c = self.get(c.parent_category_id)
            if d > 50:
                break
        return d

    def recompute_paths(self) -> None:
        for c in self.categories.values():
            parts, cur, guard = [], c, 0
            while cur is not None and guard < 50:
                parts.append(safe_component(cur.category_name))
                cur = self.get(cur.parent_category_id)
                guard += 1
            c.relative_path = "/".join(reversed(parts))

    def content_signature(self) -> dict:
        """時刻を除いた内容（変更有無の判定に使う）。"""
        cats = []
        for c in self.ordered():
            d = asdict(c)
            d.pop("created_at", None)
            d.pop("updated_at", None)
            cats.append(d)
        return {"categories": cats}


class TaxonomyError(ValueError):
    pass


def _spec_to_category(spec: dict, ts: str) -> Category:
    name = str(spec.get("category_name") or spec.get("name") or "").strip()
    if not name:
        raise TaxonomyError("カテゴリ名が空です")
    rules = spec.get("classification_rules") or {}
    keywords = [str(k) for k in (rules.get("keywords") or spec.get("keywords") or [])][:30]
    weights = {str(k): float(v) for k, v in (rules.get("keyword_weights") or {}).items()}
    return Category(
        category_id=spec.get("category_id") or new_category_id(),
        category_name=name[:80],
        parent_category_id=spec.get("parent_category_id"),
        description=str(spec.get("description", ""))[:1000],
        classification_rules={
            "keywords": keywords,
            "keyword_weights": weights,
            "filename_patterns": [str(p) for p in rules.get("filename_patterns", [])][:20],
            "notes": str(rules.get("notes", ""))[:500],
        },
        exclusion_rules=[str(x) for x in (spec.get("exclusion_rules") or [])][:20],
        examples=[str(x) for x in (spec.get("examples") or [])][:10],
        created_at=ts,
        updated_at=ts,
    )


def apply_operations(tax: Taxonomy, operations: list[dict]) -> Taxonomy:
    """操作列を検証付きで適用した新しい Taxonomy を返す（元は変更しない）。

    サポートする操作:
      add / rename / merge / split / move / update / archive
    """
    new = tax.clone()
    ts = now_iso()
    # add は親を先に処理する
    ops = sorted(enumerate(operations), key=lambda iv: (_op_order(iv[1], operations), iv[0]))
    for _, op in ops:
        kind = op.get("op")
        if kind == "add":
            cat = _spec_to_category(op["category"], ts)
            if cat.category_id in new.categories:
                raise TaxonomyError(f"カテゴリ ID が既に存在します: {cat.category_id}")
            _check_parent(new, cat.parent_category_id)
            new.categories[cat.category_id] = cat
        elif kind == "rename":
            cat = _require_active(new, op["category_id"])
            name = str(op.get("new_name", "")).strip()
            if not name:
                raise TaxonomyError("新しいカテゴリ名が空です")
            cat.category_name = name[:80]
            cat.updated_at = ts
        elif kind == "update":
            cat = _require_active(new, op["category_id"])
            if "description" in op:
                cat.description = str(op["description"])[:1000]
            if "keywords" in op:
                cat.classification_rules["keywords"] = [str(k) for k in op["keywords"]][:30]
                cat.classification_rules["keyword_weights"] = {
                    k: v for k, v in cat.classification_rules.get("keyword_weights", {}).items()
                    if k in cat.classification_rules["keywords"]
                }
            if "exclusion_rules" in op:
                cat.exclusion_rules = [str(x) for x in op["exclusion_rules"]][:20]
            if "examples" in op:
                cat.examples = [str(x) for x in op["examples"]][:10]
            cat.updated_at = ts
        elif kind == "merge":
            target = _require_active(new, op["target_id"])
            for sid in op["source_ids"]:
                if sid == target.category_id:
                    continue
                src = _require_active(new, sid)
                if _is_descendant(new, target.category_id, sid):
                    raise TaxonomyError("統合先が統合元の子孫になっています")
                for child in new.children(sid):
                    child.parent_category_id = target.category_id
                    child.updated_at = ts
                kw = target.keywords
                for k in src.keywords:
                    if k not in kw:
                        kw.append(k)
                target.classification_rules["keywords"] = kw[:30]
                tw = target.classification_rules.setdefault("keyword_weights", {})
                for k, v in src.keyword_weights.items():
                    tw[k] = max(tw.get(k, 0.0), v)
                target.examples = (target.examples + src.examples)[:10]
                src.approval_status = "merged"
                src.merged_into = target.category_id
                src.updated_at = ts
            target.updated_at = ts
        elif kind == "split":
            parent = _require_active(new, op["category_id"])
            if not op.get("children"):
                raise TaxonomyError("分割後のカテゴリが指定されていません")
            for spec in op["children"]:
                spec = dict(spec)
                spec["parent_category_id"] = parent.category_id
                cat = _spec_to_category(spec, ts)
                if cat.category_id in new.categories:
                    raise TaxonomyError(f"カテゴリ ID が既に存在します: {cat.category_id}")
                new.categories[cat.category_id] = cat
            parent.updated_at = ts
        elif kind == "move":
            cat = _require_active(new, op["category_id"])
            pid = op.get("new_parent_id")
            _check_parent(new, pid)
            if pid and (pid == cat.category_id or _is_descendant(new, pid, cat.category_id)):
                raise TaxonomyError("自分自身または子孫の下には移動できません")
            cat.parent_category_id = pid
            cat.updated_at = ts
        elif kind == "archive":
            cat = _require_active(new, op["category_id"])
            if new.children(cat.category_id):
                raise TaxonomyError("子カテゴリがあるカテゴリはアーカイブできません")
            cat.approval_status = "archived"
            cat.updated_at = ts
        else:
            raise TaxonomyError(f"未対応の操作です: {kind}")
    new.recompute_paths()
    _check_unique_paths(new)
    return new


def _op_order(op: dict, all_ops: list[dict]) -> int:
    if op.get("op") != "add":
        return 1000
    # 同じ操作列内で追加される親の深さに応じて並べる
    ids = {o["category"].get("category_id"): o for o in all_ops if o.get("op") == "add"}
    depth, pid, guard = 0, op["category"].get("parent_category_id"), 0
    while pid in ids and guard < 50:
        depth += 1
        pid = ids[pid]["category"].get("parent_category_id")
        guard += 1
    return depth


def _require_active(t: Taxonomy, cid: str) -> Category:
    c = t.get(cid)
    if c is None:
        raise TaxonomyError(f"カテゴリが存在しません: {cid}")
    if not c.active:
        raise TaxonomyError(f"有効なカテゴリではありません: {c.category_name}")
    return c


def _check_parent(t: Taxonomy, pid: str | None) -> None:
    if pid is None:
        return
    p = t.get(pid)
    if p is None or not p.active:
        raise TaxonomyError(f"親カテゴリが未承認または存在しません: {pid}")


def _is_descendant(t: Taxonomy, cid: str, ancestor_id: str) -> bool:
    c, guard = t.get(cid), 0
    while c and c.parent_category_id and guard < 50:
        if c.parent_category_id == ancestor_id:
            return True
        c = t.get(c.parent_category_id)
        guard += 1
    return False


def _check_unique_paths(t: Taxonomy) -> None:
    seen: dict[str, str] = {}
    for c in t.active():
        key = c.relative_path.lower()
        if key in seen:
            raise TaxonomyError(f"同じ場所に同名のカテゴリがあります: {c.relative_path}")
        seen[key] = c.category_id
