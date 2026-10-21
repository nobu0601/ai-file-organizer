"""folder_map.md（人間向け説明書）の生成。"""
from __future__ import annotations

from taxonomy.model import Taxonomy

STATUS_LABEL = {"approved": "承認済み", "merged": "統合済み", "archived": "アーカイブ"}


def render_markdown(tax: Taxonomy, pending_proposals: list[str]) -> str:
    lines = [
        "# フォルダ体系（AI File Organizer）",
        "",
        f"- 分類体系のバージョン: **v{tax.taxonomy_version}**",
        f"- 作成日時: {tax.generated_at}",
        f"- 更新日時: {tax.updated_at}",
        f"- 有効なカテゴリ数: {len(tax.active())}",
        "",
        "このファイルはアプリが自動生成します。正式に承認された分類体系だけを記載しています。",
        "",
        "## フォルダ階層",
        "",
    ]
    active = tax.active()
    if not active:
        lines.append("_（承認済みのカテゴリはまだありません）_")
    for c in active:
        indent = "  " * tax.depth(c.category_id)
        lines.append(f"{indent}- 📁 **{c.category_name}/** `{c.category_id}`")
    lines += ["", "## カテゴリ詳細", ""]
    for c in active:
        lines += [
            f"### {c.relative_path}",
            "",
            f"- カテゴリID: `{c.category_id}`",
            f"- 承認状態: {STATUS_LABEL.get(c.approval_status, c.approval_status)}",
            f"- 目的・格納対象: {c.description or '（未記入）'}",
            f"- 分類条件（キーワード）: {', '.join(c.keywords) or '（なし）'}",
        ]
        pats = c.classification_rules.get("filename_patterns") or []
        if pats:
            lines.append(f"- ファイル名パターン: {', '.join(pats)}")
        if c.exclusion_rules:
            lines.append(f"- 除外条件: {', '.join(c.exclusion_rules)}")
        if c.examples:
            lines.append("- 代表的なファイル例:")
            lines += [f"  - {e}" for e in c.examples[:5]]
        lines += [f"- 作成日時: {c.created_at}", f"- 更新日時: {c.updated_at}", ""]
    inactive = [c for c in tax.ordered() if not c.active]
    if inactive:
        lines += ["## 統合・アーカイブ済みのカテゴリ", ""]
        for c in inactive:
            target = tax.get(c.merged_into)
            suffix = f" → {target.category_name}" if target else ""
            lines.append(f"- {c.category_name} `{c.category_id}`（{STATUS_LABEL.get(c.approval_status)}{suffix}）")
        lines.append("")
    lines += ["## 未承認の提案", ""]
    if pending_proposals:
        lines.append("以下は AI の提案です。承認されるまで正式な体系には反映されません。")
        lines.append("")
        lines += [f"- {p}" for p in pending_proposals]
    else:
        lines.append("_（なし）_")
    lines.append("")
    return "\n".join(lines)
