# 分類精度の評価結果

## 1. 分類体系の考案（seed ファイルから）

| 方式 | カテゴリ数 | 作られたテーマ | 純度 | ペアF1 | 未分類 | 所要時間 |
|---|---|---|---|---|---|---|
| 内容ベース（LLM なし） | 5 | 5/8 | 94% | 44% | 22 | 0.1秒 |
| Ollama qwen3:4b（途中で LLM エラーあり） | 5 | 5/8 | 94% | 44% | 22 | 73.4秒 |
| Ollama qwen3:8b | 5 | 5/8 | 94% | 44% | 22 | 91.4秒 |

## 2. 新規ファイルの分類（承認後）

| 方式 | 正解率 | 名前あり | 名前に手掛かりなし | 自動移動（正しい/件数） | 自動移動の精度 | 無関係ファイルの誤移動 | 所要時間 |
|---|---|---|---|---|---|---|---|
| 内容ベース（LLM なし） | 19% | 28% (18件) | 7% (14件) | 0/0 | — | 0/8 | 0.3秒 |
| Ollama qwen3:4b（途中で LLM エラーあり） | 12% | 17% (18件) | 7% (14件) | 1/1 | 100% | 0/8 | 882.6秒 |
| Ollama qwen3:8b | 44% | 44% (18件) | 43% (14件) | 1/1 | 100% | 0/8 | 1206.4秒 |

### 内容ベース（LLM なし） の詳細

カテゴリ: Alpha 電流, Sweep Train, 下書 遠足, 伏見 嵐山, 工事 内装

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
| 旅行_食事候補リスト.csv | kyoto_trip | school ✗ | review | 0.47 |
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

### Ollama qwen3:4b（途中で LLM エラーあり） の詳細

カテゴリ: ..., alpha_制御システム, 京都観光行程, 内装・設備打合せ, 学生遠足準備

| ファイル | 正解 | 予測 | 状態 | 信頼度 |
|---|---|---|---|---|
| alpha_基板_発熱評価.xlsx | alpha_motor | — ✗ | review | 0.00 |
| 無題.md | alpha_motor | — ✗ | review | 0.00 |
| scan_0012.txt | alpha_motor | ml_research ✗ | review | 0.45 |
| alpha_ファームウェア_起動ログ.log | alpha_motor | alpha_motor ✓ | review | 0.66 |
| 2026年度_消費税集計.csv | accounting | ml_research ✗ | review | 0.47 |
| export.csv | accounting | — ✗ | review | 0.00 |
| document (3).docx | accounting | — ✗ | review | 0.00 |
| 月次_売掛金消込メモ.md | accounting | — ✗ | review | 0.00 |
| 採用フロー_改善案.html | hiring | — ✗ | review | 0.00 |
| 新しいテキスト ドキュメント.txt | hiring | — ✗ | review | 0.00 |
| document (7).docx | hiring | — ✗ | review | 0.00 |
| 応募者一覧_2026夏.csv | hiring | — ✗ | review | 0.00 |
| 実験ノート_データ拡張.docx | ml_research | — ✗ | review | 0.00 |
| output.txt | ml_research | ml_research ✓ | review | 0.47 |
| export_1.csv | ml_research | ml_research ✓ | moved | 0.87 |
| dropout_ablation_summary.html | ml_research | ml_research ✓ | review | 0.68 |
| 京都_移動手段_比較.html | kyoto_trip | — ✗ | review | 0.00 |
| 無題 (4).md | kyoto_trip | — ✗ | review | 0.00 |
| scan_0031.txt | kyoto_trip | — ✗ | review | 0.00 |
| 旅行_食事候補リスト.csv | kyoto_trip | — ✗ | review | 0.00 |
| 内装業者_追加工事の相談.html | renovation | — ✗ | review | 0.00 |
| memo_0511.txt | renovation | — ✗ | review | 0.00 |
| data (2).csv | renovation | ml_research ✗ | review | 0.42 |
| 壁紙・床材_選定メモ.md | renovation | — ✗ | review | 0.00 |
| 給食だより_献立表.xlsx | school | — ✗ | review | 0.00 |
| scan_0077.txt | school | — ✗ | review | 0.00 |
| 無題2.txt | school | — ✗ | review | 0.00 |
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
| ギター練習_コード表.xlsx | none | ml_research ✗ | review | 0.47 |
| 引っ越し_住所変更手続き.html | none | — — | review | 0.00 |
| ゲーム攻略_進行ログ.log | none | ml_research ✗ | review | 0.47 |

### Ollama qwen3:8b の詳細

カテゴリ: alpha制御, 下書き_遠足資料, 京都観光計画, 内装工事管理, 学習結果

| ファイル | 正解 | 予測 | 状態 | 信頼度 |
|---|---|---|---|---|
| alpha_基板_発熱評価.xlsx | alpha_motor | alpha_motor ✓ | review | 0.76 |
| 無題.md | alpha_motor | alpha_motor ✓ | review | 0.45 |
| scan_0012.txt | alpha_motor | alpha_motor ✓ | review | 0.42 |
| alpha_ファームウェア_起動ログ.log | alpha_motor | alpha_motor ✓ | review | 0.66 |
| 2026年度_消費税集計.csv | accounting | — ✗ | review | 0.00 |
| export.csv | accounting | renovation ✗ | review | 0.30 |
| document (3).docx | accounting | — ✗ | review | 0.00 |
| 月次_売掛金消込メモ.md | accounting | — ✗ | review | 0.00 |
| 採用フロー_改善案.html | hiring | school ✗ | review | 0.42 |
| 新しいテキスト ドキュメント.txt | hiring | — ✗ | review | 0.00 |
| document (7).docx | hiring | — ✗ | review | 0.00 |
| 応募者一覧_2026夏.csv | hiring | school ✗ | review | 0.40 |
| 実験ノート_データ拡張.docx | ml_research | ml_research ✓ | review | 0.56 |
| output.txt | ml_research | ml_research ✓ | review | 0.47 |
| export_1.csv | ml_research | ml_research ✓ | moved | 0.87 |
| dropout_ablation_summary.html | ml_research | ml_research ✓ | review | 0.68 |
| 京都_移動手段_比較.html | kyoto_trip | kyoto_trip ✓ | review | 0.59 |
| 無題 (4).md | kyoto_trip | kyoto_trip ✓ | review | 0.47 |
| scan_0031.txt | kyoto_trip | kyoto_trip ✓ | review | 0.47 |
| 旅行_食事候補リスト.csv | kyoto_trip | kyoto_trip ✓ | review | 0.66 |
| 内装業者_追加工事の相談.html | renovation | renovation ✓ | review | 0.47 |
| memo_0511.txt | renovation | kyoto_trip ✗ | review | 0.45 |
| data (2).csv | renovation | renovation ✓ | review | 0.30 |
| 壁紙・床材_選定メモ.md | renovation | — ✗ | review | 0.00 |
| 給食だより_献立表.xlsx | school | kyoto_trip ✗ | review | 0.42 |
| scan_0077.txt | school | — ✗ | review | 0.00 |
| 無題2.txt | school | — ✗ | review | 0.00 |
| 保護者面談_日程調整.md | school | — ✗ | review | 0.00 |
| 野菜の生育_観察日記.html | garden | — ✗ | review | 0.00 |
| 新規 Microsoft Excel ワークシート.xlsx | garden | — ✗ | review | 0.00 |
| untitled.html | garden | — ✗ | review | 0.00 |
| 畑_水やり当番.md | garden | — ✗ | review | 0.00 |
| 猫の飼育_記録.txt | none | school ✗ | review | 0.30 |
| 料理レシピ_カレー.md | none | — — | review | 0.00 |
| 読書感想_小説.docx | none | — — | review | 0.00 |
| 車の点検_予定.csv | none | school ✗ | review | 0.30 |
| 映画メモ_秋.txt | none | — — | review | 0.00 |
| ギター練習_コード表.xlsx | none | school ✗ | review | 0.30 |
| 引っ越し_住所変更手続き.html | none | — — | review | 0.00 |
| ゲーム攻略_進行ログ.log | none | school ✗ | review | 0.40 |
