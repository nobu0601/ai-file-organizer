"""folder_map.json の JSON Schema。"""
from __future__ import annotations

CATEGORY_SCHEMA = {
    "type": "object",
    "required": [
        "category_id", "category_name", "parent_category_id", "relative_path", "description",
        "classification_rules", "exclusion_rules", "approval_status", "created_at", "updated_at",
    ],
    "properties": {
        "category_id": {"type": "string", "pattern": "^cat-[0-9a-f]{12}$"},
        "category_name": {"type": "string", "minLength": 1, "maxLength": 80},
        "parent_category_id": {"type": ["string", "null"]},
        "relative_path": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "classification_rules": {
            "type": "object",
            "required": ["keywords"],
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "keyword_weights": {"type": "object", "additionalProperties": {"type": "number"}},
                "filename_patterns": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
        },
        "exclusion_rules": {"type": "array", "items": {"type": "string"}},
        "approval_status": {"enum": ["approved", "merged", "archived"]},
        "merged_into": {"type": ["string", "null"]},
        "examples": {"type": "array", "items": {"type": "string"}},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
    },
}

FOLDER_MAP_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AIFileOrganizer folder_map",
    "type": "object",
    "required": ["taxonomy_version", "generated_at", "updated_at", "categories"],
    "properties": {
        "taxonomy_version": {"type": "integer", "minimum": 0},
        "generated_at": {"type": "string"},
        "updated_at": {"type": "string"},
        "categories": {"type": "array", "items": CATEGORY_SCHEMA},
    },
}


def validate_folder_map(data: dict) -> None:
    """不正なら jsonschema.ValidationError / ValueError を送出する。"""
    import jsonschema

    jsonschema.validate(data, FOLDER_MAP_SCHEMA)
    ids = [c["category_id"] for c in data["categories"]]
    if len(ids) != len(set(ids)):
        raise ValueError("category_id が重複しています")
    idset = set(ids)
    for c in data["categories"]:
        pid = c.get("parent_category_id")
        if pid is not None and pid not in idset:
            raise ValueError(f"存在しない親カテゴリを参照しています: {pid}")
