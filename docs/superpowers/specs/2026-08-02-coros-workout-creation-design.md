# `create_workouts.py` — 從計畫文件建立並驗證 COROS 課表

- 日期：2026-08-02
- 狀態：設計已核可，待實作
- 觸發事件：2026-08-02 建立 W32 週日長跑時連續踩到兩個單位錯誤（見下方「問題」）

## 問題

用手工組裝的 MCP 呼叫建立跑步課表，同一天內連續產生兩次錯誤：

1. **配速全錯**：傳 `create_run_workout` 的 `pace` 人類可讀字串（`"4:40-4:50/km"`）＋ `intensity_label: "Pace"`，四個步驟的 `intensity_value`/`intensity_value_extend` **全部塌成同一組 `Pace` preset 預設值 `186411/223694`**（≈3:06-3:44/km）。API 回傳成功，無任何錯誤或警告。若未讀回檢查，使用者會在錶上看到一堂 24km 輕鬆長跑標著 3:06/km。
2. **距離顯示成英里**：修正配速時只設了 `intensity_display_unit: 1`，漏掉 `target_display_unit: 1`。課表標題是自行輸入的字串所以仍寫「24km」，但分段距離全部以英里呈現——使用者回報「分段距離變成英里，但 TITLE 是公里」才發現。

第 2 點的規則 `wiki/concepts/coros-unit-pitfall.md`「結論」一節早已寫明，修正時還引用了該頁，仍然漏掉。**結論：知道規則不足以防止錯誤，缺的是機械化的驗證步驟。**

### 根因分析（讀 upstream 原始碼確認）

`coros-training-mcp` v0.2.0 為三層結構：

| 層 | 檔案 | 狀態 |
|---|---|---|
| MCP 工具層 | `run_workout_schema.py`、`pace_parser.py` | **`pace` 字串／`intensity_label` 展開在此，即問題 1 的來源** |
| API 層 | `coros_api.py` | 只吃 raw 欄位，**完全無 `pace` 概念**（grep `"pace"` 零命中） |
| COROS HTTP API | — | — |

問題 2 是 API 層的預設值，**不是呼叫端遺漏設定**：

```python
# coros_api._resolve_run_target()
target_display_unit = int(step.get("target_display_unit", 3))   # 預設 3 = 英里

# coros_api.build_run_workout_payload()
"distanceDisplayUnit": 3,                                        # 硬寫
"targetDisplayUnit": 3 if group_target_type in DISTANCE_TARGET_TYPES else 0,
```

不論走 MCP 或走 Python，不明確傳 `1` 就一定是英里。

## 目標與非目標

**目標**
- 建立週日長跑這類**新建**跑步課表時，配速與顯示單位不可能出錯
- 驗證失敗時，行事曆不被觸碰
- 計畫文件與錶上實際內容之間沒有人工轉錄環節

**非目標（本次明確排除）**
- 不接管整週排程。週間沿用既有 library workout 的課表（含肌力）維持現行手動 MCP 排程作法——課表庫存在同名項目（三筆「長距離跑 20km 純輕鬆 (週日)」、三筆「輕鬆跑 10km (週二)」，其中含 `estimated_time` 為 `3375000s` 的舊壞資料），靠名稱比對重用會引入新的歧義風險，不值得。
- 不修 upstream `pace_parser.py`。uv 安裝的套件會被 `uv tool upgrade` 蓋掉，且無法解決英里預設。（另行回報 issue 是好事，但不作為防線。）
- 不處理肌力課表。

## 架構

四個純函式 + 一層薄網路呼叫：

```
plan doc ──parse_plan()──► [Step] ──build_payload()──► dict
                                                        │
                                             create ────┤ (coros_api)
                                                        ▼
                                                   read back
                                                        │
                                           verify() ◄───┘ → [problem]
                                                        │ 空才繼續
                                                   schedule
```

前三個函式為純函式，可用真實的 `doc/2026-W32-training-plan.md` 當測試資料，不需網路。

### 元件

| 元件 | 職責 | 依賴 |
|---|---|---|
| `parse_plan(text) -> PlanEntry` | markdown 文字 → 日期／課表名稱／步驟清單 | 無（純字串處理） |
| `build_steps(rows) -> [dict]` | 表格列 → `coros_api` 步驟 dict，**顯示單位寫死 1** | 無 |
| `verify(workout, expected) -> [str]` | 讀回的課表 dict ＋ `build_steps` 產出的期望步驟 → 問題描述清單（空 = 通過） | 無 |
| `main()` | 串接、印進度、處理重跑、非零退出 | `coros_api` |

`build_steps` 是防呆的所在：呼叫端無法表達「英里」，因為函式不接受該參數。

## 計畫文件契約

在分段表前加一行 HTML 註解當錨點。渲染後不可見，人類版面不受影響：

```markdown
### 08-09 長跑分段（重點課）

<!-- coros: day=2026-08-09 name=長距離跑 24km 平路含4km馬配 (週日) -->

| 段落 | 距離 | 配速 | 目的 |
|---|---|---|---|
| 熱身 | 8km | 5:40-5:55/km | 不要衝開頭 |
| Easy | 6km | 5:30-5:45/km | 過渡 |
| **馬配段** | **4km** | **4:45/km**（不追 4:37） | 首次在疲勞下鎖速 |
| 收操 | 6km | 5:40-6:00/km | 觀察跑姿保持 |
```

**規則**
- 錨點格式：`<!-- coros: day=YYYY-MM-DD name=<課表名稱> -->`，其後第一個 markdown 表格即為步驟來源。
- 表頭必須含「距離」與「配速」兩欄；其餘欄位（段落、目的…）忽略。
- **距離欄為分段長度**（`8km`），非累積區間（`0-8km`）。累積區間需相減還原，多一個出錯點——這是相對現行文件唯一的格式改動。
- 配速欄接受區間 `5:40-5:55/km` 或單值 `4:45/km`。**單值展開為 ±5 秒**（`4:45/km` → `4:40-4:50/km`），展開結果會印出供確認。
- 解析器剝除 `**` 粗體標記與括號註解，因此表格內仍可寫人類說明。
- 步驟 kind 依位置推斷：第一列 `warmup`、最後一列 `cooldown`、其餘 `training`。段落名稱含「熱身」/「收操」時以名稱為準。
- 一份計畫文件可含多個錨點（多堂新建課表），全部處理。

## 驗證（四項，任一不過即中止）

逐步檢查讀回的課表，期望值取自 `build_steps` 的輸出（同一份資料送出、讀回、比對，不另行硬編）：

1. `intensity_value` / `intensity_value_extend` 與期望值逐步相符，**且指定了不同配速的步驟之間數值必須相異**（全部相同 = `pace` 參數 bug 的指紋）
2. `intensity_display_unit == 1`
3. `target_display_unit == 1`（否則距離顯示為英里）
4. `estimated_time_seconds` 量級合理（與距離×配速自行計算值相差 20% 內）

第 1 與第 4 項互為佐證：配速編碼正確時 `estimated_time_seconds` 才會正常（2026-08-02 實測：ms/km 版本回傳 4921260，秒/km 版本回傳 8045 = 2:14:05，正確）。

**失敗處理**：印出哪一步、哪一項不符、期望值與實際值 → `delete_workout` 刪除剛建立的壞課表 → 非零退出。**行事曆不被觸碰。**

## 重跑行為

同日同名已排程時：`remove_scheduled_workout` → `delete_workout`（舊課表）→ 建新的 → 驗證 → 排程。每一步印出。

此決定為使用者明確選擇（相對於「保留舊課表」或「拒絕並要求 `--force`」），理由是避免課表庫累積孤兒——2026-08-02 一次迭代就留下兩個廢棄課表（`479337470933254243`、`479337509856395566`）。

## 錯誤處理

| 情況 | 行為 |
|---|---|
| 找不到錨點 | 非零退出，印出「文件中無 `<!-- coros:` 錨點」 |
| 表格缺欄位／格式不符 | 非零退出，印出行號與該列原文 |
| 配速無法解析 | 非零退出，印出該列與接受格式 |
| 驗證不過 | 刪除新建課表，非零退出（見上） |
| 網路／認證失敗 | 讓 `coros_api` 的例外往上拋，附一行提示跑 `coros-mcp auth-status` |

## 測試

純函式以真實文件為測試資料：

- `parse_plan`：對 `doc/2026-W32-training-plan.md` 解析出正確的日期、名稱、4 個步驟；錨點缺失／表格畸形時拋出明確錯誤
- `build_steps`：**斷言產出的每一步 `target_display_unit == 1` 且 `intensity_display_unit == 1`**；配速字串正確轉為秒/公里；單值配速正確展開為 ±5 秒
- `verify`：對 2026-08-02 實際踩到的兩個壞課表形狀各給一個 fixture（四步同值的 ms/km 版本、`target_display_unit: 3` 的英里版本），斷言兩者都被攔下並指出正確原因；對修好的版本斷言回傳空清單

網路層（create/read-back/schedule/delete）不做單元測試，以 `--dry-run` 手動驗證。

## 使用方式

```bash
python create_workouts.py --plan doc/2026-W32-training-plan.md
python create_workouts.py --plan doc/2026-W32-training-plan.md --dry-run   # 只印解析結果與 payload，不連線
```

置於 repo 根目錄，與 `fetch_training_data.py` 同層同模式（`_relaunch_in_tool_venv()` → `import coros_api` → argparse）。

## 後續

實作完成後需更新：
- `README.md` — 新增此腳本說明
- `.claude/skills/marathon-coach/SKILL.md` 步驟 6 — 改為「用 `create_workouts.py` 建立新課表」，而非手工組 MCP 呼叫
- `wiki/concepts/coros-unit-pitfall.md` — 註明防線已機械化
