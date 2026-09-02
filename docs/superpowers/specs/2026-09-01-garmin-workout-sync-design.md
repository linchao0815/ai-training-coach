# Garmin 課表同步設計（2026-09-01）

## 背景與目的

`create_workouts.py` 已經能把 `doc/YYYY-WNN-training-plan.md` 裡 `<!-- coros: day=... name=... -->`
錨點標記的跑步課表，解析、建立並排上 COROS Training Hub 的行事曆，建立後強制讀回驗證
（見 `coros_workout_plan.py` 的 `verify()`）。

Garmin 這邊目前只有 `fetch_garmin_data.py`，方向是單向、唯讀（把 Garmin Connect 的歷史資料
拉回本地）。跑者現在同時使用 COROS 與 Garmin 裝置，需要把**同一份**訓練計畫課表也推上
Garmin Connect 的行事曆，讓 Garmin 裝置的使用者也能照表操課、錶上有結構化提示（配速區間、
分段距離）。

存在的理由跟 `create_workouts.py` 一致：手動在 Garmin Connect 網頁/App 上一步步建立課表容易
出錯（配速、距離單位、行事曆日期），而且兩邊維護會漂移。

## 範疇

**這次只做**：把訓練計畫 markdown 裡的距離型分段課表（跟 COROS 那邊支援的格式完全一樣：
熱身/訓練/收操分段，各自「距離 + 配速區間」）推上 Garmin Connect，並排上指定日期的行事曆。

**明確不做**（YAGNI，之後有需要再加）：
- 時間型步驟、repeat/interval 群組（跟 COROS 那邊 `coros_workout_plan.py` 目前的限制一致）
- 重訓/游泳/騎車等非跑步 workout 類型
- 把 Garmin 上已存在的課表讀回來跟 COROS 或 markdown 做雙向比對
- 自動化排程觸發（例如 commit 後自動推送）——維持跟 COROS 一樣的手動 CLI 呼叫模式

## 架構總覽

沿用 COROS 那條路徑的三層拆分，一一對應：

```
doc/YYYY-WNN-training-plan.md
        │
        │ <!-- coros: day=... name=... --> 錨點 + 表格（兩邊共用同一份，不新增語法）
        ▼
coros_workout_plan.py           ← 既有，不修改
  parse_plan() -> [PlanEntry(day, name, segments=[Segment(...), ...])]
        │
        ├──────────────────────────────┐
        ▼                               ▼
coros_workout_plan.build_steps()   garmin_workout_plan.py（新增）
  -> COROS step dicts                 build_steps(segments) -> Garmin step 結構
        │                               verify(workout, expected_steps) -> [problem, ...]
        ▼                               ▼
coros_runner.py（既有）             garmin_runner.py（新增）
  建立 → 讀回驗證 → 排行事曆           建立 → 讀回驗證 → 排行事曆
        │                               │
        ▼                               ▼
create_workouts.py（既有）          create_garmin_workouts.py（新增）
  CLI: --plan <file> [--dry-run]     CLI: --plan <file> [--dry-run]（用法完全對稱）
```

`.claude/skills/garmin-sync/SKILL.md`（新增）記錄這條流程怎麠用、已知限制、跟
`marathon-coach` skill 的分工邊界（`marathon-coach` 規劃出週課表 markdown 之後，
課表確認的推送動作交給這個 skill；`marathon-coach` 本身不重複這些細節，只在它的流程裡
提一句「COROS 用 create_workouts.py，Garmin 用這個新 skill」）。

## 元件細節

### 1. `garmin_workout_plan.py`（純函式，標準庫，可獨立單元測試）

只 import 標準庫，**不 import `garminconnect`**——理由跟 `coros_workout_plan.py` 的
docstring 一樣：讓測試不需要裝 Garmin 那個 venv/套件就能跑。

- `build_steps(segments: list[Segment]) -> list[dict]`
  把 `coros_workout_plan.Segment`（name/kind/distance_m/pace_fast_s/pace_slow_s）轉成
  Garmin 課表步驟需要的最小結構描述（純資料 dict，不依賴 `garminconnect.workout` 的
  pydantic 類別——那些類別留在 `garmin_runner.py` 建構，因為它們需要裝 `pydantic`/
  `garminconnect`）。每個 dict 至少含：
  - `kind`（warmup / training / cooldown，沿用 COROS 那邊 `Segment.kind` 的值）
  - `name`
  - `distance_m`
  - `pace_fast_s` / `pace_slow_s`（秒/公里，跟 `Segment` 欄位單位一致，**不**在這一層
    做速度轉換——m/s 轉換是 Garmin API 專屬的编碼細節，留給 `garmin_runner.py`，
    這樣 `verify()` 才能直接拿使用者看得懂的秒/公里比較，不用先轉一次單位再轉回來）

- `expected_duration_seconds(steps)` — 沿用跟 COROS 一樣的算法（距離 × 中位配速加總），
  用來跟 Garmin 回傳的預估時長做合理性檢查。

- `verify(workout: dict, expected_steps: list[dict]) -> list[str]`
  比照 `coros_workout_plan.verify()` 的精神，讀回 `garmin_runner.upload()` 回傳/
  `get_workout_by_id()` 讀到的 workout 結構，逐步驟核對：
  - 步驟數是否相符
  - 每步驟的結束條件是距離、且距離值（换算回公尺後）跟預期相符
  - 每步驟的 target type 是 PACE_ZONE，且換算回秒/公里後的配速區間跟預期相符
    （**這一項是本設計最大的未知數**——`targetValueOne`/`targetValueTwo` 的方向與
    m/s↔秒/公里換算公式沒有官方文件或套件範例可查，第一次實作時必須用一個已知課表
    實際建立、讀回、人工核對 Garmin App 上顯示的配速，找出正確換算方式後才能把
    `verify()` 的比對邏輯寫死；在確認前，`garmin_runner.py` 的建立動作一律視為
    「未驗證，不可排上行事曆」）
  - 步驟配速不能全部相同（沿用 COROS 那邊抓「pace 參數塌成 preset 預設值」這種指紋
    的檢查邏輯，防同類型的靜默壞掉）

### 2. `garmin_runner.py`（網路層，需要 `garminconnect` + `pydantic`）

- 認證：重用 `fetch_garmin_data.py` 已經寫好的 `get_client()` 邏輯（`GARMIN_EMAIL`/
  `GARMIN_PASSWORD` 環境變數 + `~/.garminconnect/` session cache）。**視需要把
  `fetch_garmin_data.py` 裡跟認證/client 建構相關的邏輯抽成一個小的共用模組**
  （例如 `garmin_auth.py`），避免兩支腳本各自維護一份幾乎一樣的登入程式碼——
  這屬於「順手改善既有程式碼」，不是額外範疇擴張。
- `build_workout(entry_name, steps) -> RunningWorkout`：把 `garmin_workout_plan.build_steps()`
  的純資料 dict 轉成 `garminconnect.workout.RunningWorkout`/`WorkoutSegment`/
  `ExecutableStep` 物件（用 `create_distance_interval_step`/`create_warmup_step`/
  `create_cooldown_step` 等既有 helper，`targetType` 手動組 PACE_ZONE 字典）。
- `run(plans)`：對每個 `(entry, steps)`：
  1. 呼叫 `client.upload_running_workout(workout)` 建立
  2. 用回傳的 workout id 呼叫 `client.get_workout_by_id(id)` 讀回
  3. 呼叫 `garmin_workout_plan.verify(readback, steps)`，有任何 problem 就印出來、
     **不排上行事曆**、且該筆課表視為失敗但不中斷其餘課表的處理（沿用
     `coros_runner.py` 目前「單筆失敗不影響其他筆」的行為，需先讀 `coros_runner.py`
     確認一致寫法）
  4. 驗證通過才呼叫 `client.schedule_workout(id, entry.day)` 排上日期

### 3. `create_garmin_workouts.py`（CLI 入口）

跟 `create_workouts.py` 完全對稱的介面與行為：

```bash
python create_garmin_workouts.py --plan doc/2026-W35-training-plan.md --dry-run
python create_garmin_workouts.py --plan doc/2026-W35-training-plan.md
```

`--dry-run` 只解析並印出步驟摘要，不連線、不 import `garminconnect`（跟 `create_workouts.py`
的 `--dry-run` 行為一致，讓沒裝 Garmin venv 的人也能先檢查解析結果）。

同樣需要處理「`garminconnect` 不在目前 Python 環境」的狀況——但這裡的解法應該跟
`fetch_garmin_data.py` 一致（PEP 723 inline script metadata + `uv run`），而不是
`create_workouts.py`/`coros_runner.py` 那種「找 coros-training-mcp 的 venv 重新
執行自己」的 `_relaunch_in_tool_venv()` 模式——因為 Garmin 這邊本來就沒有一個
獨立安裝的 CLI 工具 venv 可以借用，`uv run` 才是這個 repo 對 `garminconnect`
依賴已經在用的方式。

### 4. `.claude/skills/garmin-sync/SKILL.md`（新增）

輕量 skill，內容涵蓋：
- 這條路徑跟 `marathon-coach` 的分工邊界（`marathon-coach` 產出並讓athlete 確認週課表
  markdown 之後，COROS 用 `create_workouts.py`，Garmin 用這裡）
- 使用方式（`--dry-run` 先看、確認後才正式推送——**排上使用者真實行事曆前一定要有
  這一步確認**，跟 `marathon-coach` Step 5-6 對 COROS 的處理原則一致）
- 已知限制（同 `coros_workout_plan.py` 目前的限制：只支援距離型分段，不支援
  時間型/repeat 群組）
- 配速換算的已知風險（見上方 `verify()` 段落），與「第一次用真實帳號驗證前不可
  盲目信任」的提醒

## 測試

- `tests/test_garmin_workout_plan.py`：純函式測試，比照 `tests/test_coros_workout_plan.py`
  的結構——`build_steps()` 的正常轉換、`verify()` 抓出各類不符（步驟數不符、配速不符、
  距離不符、配速全部塌成同一組這種指紋）。不需要 `garminconnect`/`pydantic`，任何
  Python 3 都能跑。
- `garmin_runner.py` 本身不寫單元測試（跟 `coros_runner.py` 一致：網路層用真實帳號
  手動驗證，見下方）。

## 首次上線的驗證步驟（非自動化，实作計畫裡要包含這一步）

配速目標欄位（`targetValueOne`/`targetValueTwo` 的方向與單位換算）沒有官方文件可查，
必須：
1. 用一筆已知課表（例如「1km @ 5:00-5:10/km」）實際呼叫 `upload_running_workout`
2. 讀回 `get_workout_by_id()` 的原始 JSON，人工比對欄位數值
3. 打開 Garmin Connect App/網頁看該課表實際顯示的配速範圍，跟表格數字核對
4. 確認換算公式後才能把 `garmin_workout_plan.verify()` 的配速比對邏輯寫成正式規則，
   並在 `garmin_runner.py` 加註解說明公式的來源（比照 `coros_workout_plan.py` 開頭
   對 COROS 兩個 display-unit 欄位的註解方式）

在這一步驗證完成前，`create_garmin_workouts.py` 正式（非 `--dry-run`）呼叫時應該在
文件裡明確標示為「配速驗證邏輯尚未經真實帳號確認」，避免課表帶著錯誤配速被排上使用者
真實的 Garmin 行事曆卻沒人發現——這正是 `create_workouts.py` 存在的理由本身
（見 docstring 開頭「兩個 bug 都上線到跑者的手錶」那段），不能在 Garmin 這邊重蹈覆轍。

## 已知限制（沿革自 COROS 那邊，原樣繼承）

- 只支援距離型分段，不支援時間型步驟或 repeat/interval 群組
- 配速表格欄位格式要求跟 COROS 完全一致（`5:40-5:55/km` 或 `4:45/km`），因為共用
  同一個 `parse_plan()`
