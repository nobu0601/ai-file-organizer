# 分類精度の評価結果

## 1. 分類体系の考案（seed ファイルから）

| 方式 | カテゴリ数 | 作られたテーマ | 純度 | ペアF1 | 未分類 | 所要時間 |
|---|---|---|---|---|---|---|
| 内容ベース（LLM なし） | 5 | 5/8 | 100% | 45% | 23 | 0.1秒 |
| Ollama qwen3:4b | 9 | 8/8 | 100% | 90% | 2 | 246.5秒 |
| Ollama qwen3:8b | 9 | 8/8 | 100% | 90% | 2 | 410.4秒 |

## 2. 新規ファイルの分類（承認後）

| 方式 | 正解率 | 名前あり | 名前に手掛かりなし | 自動移動（正しい/件数） | 自動移動の精度 | 無関係ファイルの誤移動 | 所要時間 |
|---|---|---|---|---|---|---|---|
| 内容ベース（LLM なし） | 19% | 28% (18件) | 7% (14件) | 0/0 | — | 0/8 | 0.3秒 |
| Ollama qwen3:4b | 50% | 61% (18件) | 36% (14件) | 1/1 | 100% | 0/8 | 557.9秒 |
| Ollama qwen3:8b | 81% | 83% (18件) | 79% (14件) | 1/1 | 100% | 0/8 | 849.6秒 |

### 内容ベース（LLM なし） の詳細

カテゴリ: Alpha 電流, Sweep Train, 伏見 嵐山, 工事 内装, 遠足 リスト

| ファイル | 正解 | 予測 | 状態 | 信頼度 |
|---|---|---|---|---|
| alpha_基板_発熱評価.xlsx | alpha_motor | alpha_motor ✓ | review | 0.57 |
| 無題.md | alpha_motor | — ✗ | review | 0.00 |
| scan_0012.txt | alpha_motor | — ✗ | review | 0.00 |
| alpha_ファームウェア_起動ログ.log | alpha_motor | alpha_motor ✓ | review | 0.37 |
| 2026年度_消費税集計.csv | accounting | — ✗ | review | 0.00 |
| export.csv | accounting | — ✗ | review | 0.00 |
| document (3).docx | accounting | — ✗ | review | 0.00 |
| 月次_売掛金消込メモ.md | accounting | — ✗ | review | 0.00 |
| 採用フロー_改善案.html | hiring | — ✗ | review | 0.00 |
| 新しいテキスト ドキュメント.txt | hiring | — ✗ | review | 0.00 |
| document (7).docx | hiring | — ✗ | review | 0.20 |
| 応募者一覧_2026夏.csv | hiring | — ✗ | review | 0.00 |
| 実験ノート_データ拡張.docx | ml_research | alpha_motor ✗ | review | 0.28 |
| output.txt | ml_research | — ✗ | review | 0.00 |
| export_1.csv | ml_research | ml_research ✓ | review | 0.79 |
| dropout_ablation_summary.html | ml_research | ml_research ✓ | review | 0.40 |
| 京都_移動手段_比較.html | kyoto_trip | kyoto_trip ✓ | review | 0.23 |
| 無題 (4).md | kyoto_trip | — ✗ | review | 0.00 |
| scan_0031.txt | kyoto_trip | — ✗ | review | 0.00 |
| 旅行_食事候補リスト.csv | kyoto_trip | school ✗ | review | 0.55 |
| 内装業者_追加工事の相談.html | renovation | renovation ✓ | review | 0.54 |
| memo_0511.txt | renovation | — ✗ | review | 0.00 |
| data (2).csv | renovation | — ✗ | review | 0.00 |
| 壁紙・床材_選定メモ.md | renovation | — ✗ | review | 0.00 |
| 給食だより_献立表.xlsx | school | — ✗ | review | 0.00 |
| scan_0077.txt | school | — ✗ | review | 0.00 |
| 無題2.txt | school | — ✗ | review | 0.22 |
| 保護者面談_日程調整.md | school | — ✗ | review | 0.00 |
| 野菜の生育_観察日記.html | garden | — ✗ | review | 0.00 |
| 新規 Microsoft Excel ワークシート.xlsx | garden | — ✗ | review | 0.00 |
| untitled.html | garden | — ✗ | review | 0.00 |
| 畑_水やり当番.md | garden | — ✗ | review | 0.00 |
| 猫の飼育_記録.txt | none | — — | review | 0.00 |
| 料理レシピ_カレー.md | none | — — | review | 0.00 |
| 読書感想_小説.docx | none | — — | review | 0.00 |
| 車の点検_予定.csv | none | — — | review | 0.00 |
| 映画メモ_秋.txt | none | — — | review | 0.00 |
| ギター練習_コード表.xlsx | none | — — | review | 0.00 |
| 引っ越し_住所変更手続き.html | none | — — | review | 0.00 |
| ゲーム攻略_進行ログ.log | none | — — | review | 0.00 |

### Ollama qwen3:4b の詳細

カテゴリ: ..., Alpha基板制御, ベランダ菜園_管理, リフォーム_内装工事, 京都旅行計画, 取引先管理, 運動会準備, 遠足準備, 面接・採用管理

| ファイル | 正解 | 予測 | 状態 | 信頼度 |
|---|---|---|---|---|
| alpha_基板_発熱評価.xlsx | alpha_motor | alpha_motor ✓ | review | 0.82 |
| 無題.md | alpha_motor | ml_research ✗ | review | 0.47 |
| scan_0012.txt | alpha_motor | — ✗ | review | 0.00 |
| alpha_ファームウェア_起動ログ.log | alpha_motor | — ✗ | review | 0.00 |
| 2026年度_消費税集計.csv | accounting | — ✗ | review | 0.00 |
| export.csv | accounting | accounting ✓ | review | 0.47 |
| document (3).docx | accounting | — ✗ | review | 0.00 |
| 月次_売掛金消込メモ.md | accounting | accounting ✓ | review | 0.62 |
| 採用フロー_改善案.html | hiring | hiring ✓ | review | 0.67 |
| 新しいテキスト ドキュメント.txt | hiring | hiring ✓ | review | 0.73 |
| document (7).docx | hiring | hiring ✓ | review | 0.57 |
| 応募者一覧_2026夏.csv | hiring | hiring ✓ | review | 0.59 |
| 実験ノート_データ拡張.docx | ml_research | ml_research ✓ | review | 0.59 |
| output.txt | ml_research | ml_research ✓ | review | 0.47 |
| export_1.csv | ml_research | ml_research ✓ | moved | 0.89 |
| dropout_ablation_summary.html | ml_research | ml_research ✓ | review | 0.70 |
| 京都_移動手段_比較.html | kyoto_trip | kyoto_trip ✓ | review | 0.56 |
| 無題 (4).md | kyoto_trip | — ✗ | review | 0.00 |
| scan_0031.txt | kyoto_trip | kyoto_trip ✓ | review | 0.42 |
| 旅行_食事候補リスト.csv | kyoto_trip | kyoto_trip ✓ | review | 0.72 |
| 内装業者_追加工事の相談.html | renovation | renovation ✓ | review | 0.71 |
| memo_0511.txt | renovation | — ✗ | review | 0.00 |
| data (2).csv | renovation | — ✗ | review | 0.00 |
| 壁紙・床材_選定メモ.md | renovation | — ✗ | review | 0.00 |
| 給食だより_献立表.xlsx | school | — ✗ | review | 0.00 |
| scan_0077.txt | school | — ✗ | review | 0.00 |
| 無題2.txt | school | — ✗ | review | 0.00 |
| 保護者面談_日程調整.md | school | — ✗ | review | 0.00 |
| 野菜の生育_観察日記.html | garden | garden ✓ | review | 0.58 |
| 新規 Microsoft Excel ワークシート.xlsx | garden | — ✗ | review | 0.00 |
| untitled.html | garden | — ✗ | review | 0.00 |
| 畑_水やり当番.md | garden | — ✗ | review | 0.00 |
| 猫の飼育_記録.txt | none | — — | review | 0.00 |
| 料理レシピ_カレー.md | none | — — | review | 0.00 |
| 読書感想_小説.docx | none | — — | review | 0.00 |
| 車の点検_予定.csv | none | — — | review | 0.00 |
| 映画メモ_秋.txt | none | — — | review | 0.00 |
| ギター練習_コード表.xlsx | none | ml_research ✗ | review | 0.42 |
| 引っ越し_住所変更手続き.html | none | — — | review | 0.00 |
| ゲーム攻略_進行ログ.log | none | — — | review | 0.00 |

### Ollama qwen3:8b の詳細

カテゴリ: Alpha制御基板, リフォーム工事, 京都旅行計画, 取引先関連, 学習実験, 家庭菜園, 採用プロセス, 運動会準備, 遠足準備

| ファイル | 正解 | 予測 | 状態 | 信頼度 |
|---|---|---|---|---|
| alpha_基板_発熱評価.xlsx | alpha_motor | alpha_motor ✓ | review | 0.82 |
| 無題.md | alpha_motor | alpha_motor ✓ | review | 0.47 |
| scan_0012.txt | alpha_motor | ml_research ✗ | review | 0.47 |
| alpha_ファームウェア_起動ログ.log | alpha_motor | alpha_motor ✓ | review | 0.64 |
| 2026年度_消費税集計.csv | accounting | accounting ✓ | review | 0.47 |
| export.csv | accounting | accounting ✓ | review | 0.47 |
| document (3).docx | accounting | accounting ✓ | review | 0.47 |
| 月次_売掛金消込メモ.md | accounting | accounting ✓ | review | 0.62 |
| 採用フロー_改善案.html | hiring | hiring ✓ | review | 0.67 |
| 新しいテキスト ドキュメント.txt | hiring | hiring ✓ | review | 0.73 |
| document (7).docx | hiring | hiring ✓ | review | 0.57 |
| 応募者一覧_2026夏.csv | hiring | hiring ✓ | review | 0.59 |
| 実験ノート_データ拡張.docx | ml_research | ml_research ✓ | review | 0.59 |
| output.txt | ml_research | ml_research ✓ | review | 0.42 |
| export_1.csv | ml_research | ml_research ✓ | moved | 0.89 |
| dropout_ablation_summary.html | ml_research | ml_research ✓ | review | 0.70 |
| 京都_移動手段_比較.html | kyoto_trip | kyoto_trip ✓ | review | 0.61 |
| 無題 (4).md | kyoto_trip | kyoto_trip ✓ | review | 0.47 |
| scan_0031.txt | kyoto_trip | kyoto_trip ✓ | review | 0.47 |
| 旅行_食事候補リスト.csv | kyoto_trip | kyoto_trip ✓ | review | 0.72 |
| 内装業者_追加工事の相談.html | renovation | renovation ✓ | review | 0.71 |
| memo_0511.txt | renovation | hiring ✗ | review | 0.47 |
| data (2).csv | renovation | renovation ✓ | review | 0.47 |
| 壁紙・床材_選定メモ.md | renovation | — ✗ | review | 0.00 |
| 給食だより_献立表.xlsx | school | accounting ✗ | review | 0.42 |
| scan_0077.txt | school | — ✗ | review | 0.00 |
| 無題2.txt | school | school ✓ | review | 0.42 |
| 保護者面談_日程調整.md | school | hiring ✗ | review | 0.56 |
| 野菜の生育_観察日記.html | garden | garden ✓ | review | 0.58 |
| 新規 Microsoft Excel ワークシート.xlsx | garden | garden ✓ | review | 0.47 |
| untitled.html | garden | garden ✓ | review | 0.47 |
| 畑_水やり当番.md | garden | garden ✓ | review | 0.47 |
| 猫の飼育_記録.txt | none | garden ✗ | review | 0.42 |
| 料理レシピ_カレー.md | none | garden ✗ | review | 0.55 |
| 読書感想_小説.docx | none | hiring ✗ | review | 0.30 |
| 車の点検_予定.csv | none | accounting ✗ | review | 0.42 |
| 映画メモ_秋.txt | none | kyoto_trip ✗ | review | 0.56 |
| ギター練習_コード表.xlsx | none | school ✗ | review | 0.42 |
| 引っ越し_住所変更手続き.html | none | — — | review | 0.00 |
| ゲーム攻略_進行ログ.log | none | kyoto_trip ✗ | review | 0.30 |
