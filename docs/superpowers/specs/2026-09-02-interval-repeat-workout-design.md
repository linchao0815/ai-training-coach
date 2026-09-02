# 間歇/分組課表支援設計（2026-09-02）

## 背景與目的

`coros_workout_plan.py` 的 `parse_plan()` 目前只認得「一個錨點底下正好一張 markdown 表格，
每一列是一個距離型分段」這種結構（`_find_table()` 找到錨點後第一段連續的 `|` 開頭行，
`_segments_from_table()` 把每一列轉成一個 `Segment(distance_m, pace_fast_s, pace_slow_s)`）。
`garmin_workout_plan.py` 直接重用同一份解析結果。

真實訓練計畫裡，週週都有的「獨立強度課」（例如 W36 的「5組×4分鐘 @4:15-4:25/km，組間90秒
慢跑 @6:30-7:00/km」）是**時間型、分組重複**的結構，這個 parser 完全不支援——`parse_distance()`
的正則只吃 `Nkm`，且每一列都是攤平的獨立步驟，沒有「重複 N 次」的概念。這導致這類課表至今
每週都要在對話當下手動組 COROS API 呼叫，繞過整套 markdown → 解析 → 建立 → 讀回驗證的
既有機制——沒有可重跑、可測試、可兩平台共用的路徑。

COROS、Garmin 兩邊的回傳資料裡其實都已經有分組課表的痕跡（COROS 的 `exercises[].is_group`
欄位、Garmin 的 `RepeatGroupDTO` step type），但目前程式碼都只在讀回時把它們**過濾掉**，
從沒產生過——這次要補上「產生」這一側。

本設計經過 `mattpocock-skills:grilling` 訪談定案（決策過程見對話紀錄，不重複列出逐題問答）。

## 範疇

**這次做**：
- 兩平台（COROS + Garmin）共用同一套 markdown 語法與同一個解析器
- 時間型分段（`15分鐘`、`90秒`）與現有距離型分段（`8km`）並存，同一個欄位自動判斷
- 單層重複組語法（`>>> 重複 N 組 ... <<<`），組內可放任意列數、任意混合距離型/時間型
- 分組課表比照現有距離型課表，一樣要建立後讀回驗證才能排上行事曆
- 整合進現有的 `create_workouts.py` / `create_garmin_workouts.py`，不另開新 CLI

**明確不做（YAGNI）**：
- 巢狀重複組（組中有組、金字塔結構）
- 「連續跑中穿插短暫加速」這種構造（例如「10km easy + 6×20s加速」）——形狀跟純重複組不同，
  加速段的恢復是「繼續同一段跑」而不是獨立的一列，硬塞進同一語法容易做出蹩腳的通用設計
- Garmin 端「重跑同一份課表會建立重複項目」的既有缺陷修復——與這次功能無耦合，分開排期

## 真實測試案例（驗收依據）

`doc/2026-W36-training-plan.md` 09-02（週三）：

| 段落 | 內容 | 配速 |
|---|---|---|
| 熱身 | 15 分鐘 | 5:45-6:00/km |
| 間歇 | 5 組 × 4 分鐘 | 4:15-4:25/km |
| 組間 | 90 秒慢跑 | 6:30-7:00/km |
| 收操 | 10 分鐘 | 5:45-6:00/km |

這堂課同時也是這次功能唯一要解決的真實待辦：這週三的課表目前卡著沒推上任何一個平台。

## 語法設計

### 1. 時間型分段：沿用「距離」欄，不新增欄位

現有表頭判定（`column_index("距離")`）維持不變，但欄位內容改成同時接受兩種格式，
新增一個 `parse_distance_or_duration(text) -> ("distance", meters) | ("duration", seconds)`：

```python
_DURATION_RE = re.compile(r"^(\d+)\s*分鐘$")
_SECONDS_RE = re.compile(r"^(\d+)\s*秒$")
```

不新增表頭欄位，理由：現有所有課表檔案（W30-W35 共 6 份）完全不用改表頭或格式就能繼續解析；
新增獨立欄位只會強迫回頭改所有舊檔案。

`Segment` dataclass 擴充為互斥的 `distance_m: int | None` / `duration_s: int | None`
（恰好其中一個非 None），`pace_fast_s`/`pace_slow_s` 兩種分段都必填（真實範例裡，就連時間型的
熱身、間歇、收操都帶配速目標）。

### 2. 重複組區塊：`>>> 重複 N 組 ... <<<`

```
>>> 重複 5 組
| 間歇 | 4分鐘 | 4:15-4:25/km |
| 組間 | 90秒 | 6:30-7:00/km |
<<<
```

- 區塊開頭正則：`_REPEAT_OPEN_RE = re.compile(r"^>>>\s*重複\s*(\d+)\s*組\s*$")`
- 區塊結尾：`_REPEAT_CLOSE_RE = re.compile(r"^<<<\s*$")`
- 區塊內的列**沿用外層表格已定義的三欄順序**（名稱／距離-或-時間／配速），不用也不允許自己
  的表頭——parser 看到 `>>>` 開頭行後，直接把接下來到 `<<<` 為止的每一行當表格列解析，用跟
  `_segments_from_table()` 一樣的欄位順序假設（`name_col=0`、`distance_col`/`pace_col` 沿用
  外層表格表頭算出來的索引）
- 組內列數不限制、不要求恰好兩列，每次重複就照順序執行區塊內所有列
- 列名（「間歇」「組間」）純粹是人看的標籤，parser 不做語意判斷、不套用現有的
  `_infer_kind()` 熱身/收操關鍵字邏輯——區塊內的列一律是 `kind="training"`

### 3. 一個錨點可以橫跨多個片段

現有 `_find_table()` 假設「錨點後面第一段連續的 `|` 開頭行」就是整堂課的全部內容。這次改成
`parse_plan()` 的邏輯調整為：**從錨點下一行開始，直到下一個錨點或檔尾為止，依序收集**：

- 連續的 `|` 開頭行 → 一段平坦表格片段（沿用 `_segments_from_table()`）
- `>>> 重複 N 組` 到對應 `<<<` 之間 → 一個重複組
- 其他空白/非表格行 → 忽略（沿用現有 `_find_table()` 跳過非表格行為止的行為，但改成
  「跳過就繼續找下一段」而不是「找到第一段就結束」）

`PlanEntry.segments` 欄位名稱不變（`create_workouts.py`、`create_garmin_workouts.py`、
兩邊測試檔案共 8 處直接使用這個欄位名，沒有改名的必要），但內容型別從
`list[Segment]` 擴充為 `list[Segment | RepeatGroup]`，依文件出現順序排列（範例會是
`[Segment(熱身), RepeatGroup(5, [Segment(間歇), Segment(組間)]), Segment(收操)]`）。

### 4. 資料模型

```python
@dataclass
class Segment:
    name: str
    kind: str                 # warmup / training / cooldown
    distance_m: int | None    # 與 duration_s 互斥
    duration_s: int | None
    pace_fast_s: int
    pace_slow_s: int

@dataclass
class RepeatGroup:
    repeat_count: int
    steps: list               # list[Segment]，同一組內的列，依序執行

@dataclass
class PlanEntry:
    day: str
    name: str
    segments: list             # list[Segment | RepeatGroup]，依文件出現順序（欄位名稱不變）
```

`build_steps()`（COROS、Garmin 各自現有的版本）要能吃這個混合型別的 `segments` 列表：
遇到 `Segment` 照現有邏輯攤平；遇到 `RepeatGroup`，輸出一個平台無關的中介結構——

```python
{"kind": "repeat", "repeat_count": n, "steps": [<照現有 build_steps 單列邏輯轉出的 dict>, ...]}
```

**這一層刻意停在「平台無關的中介 dict」，不直接組 COROS/Garmin 各自的真實 API 欄位**——
理由見下方「平台對應：留到實作階段」。

## 平台對應：真帳號探測結果（Task 7/8 確認，取代原「留到實作階段」規劃）

以下取代本節原本的規劃（探測步驟已在 Task 7/Task 8 執行完畢），記錄兩邊**實際**欄位形狀。

### COROS

`coros_workout_plan.py` 的 `build_steps()` 對 `RepeatGroup` 輸出的中介 dict 是：

```python
{"kind": "repeat", "repeat": N, "steps": [...]}
```

**這個形狀是 `coros_api.create_run_workout()` 的輸入合約，不是讀回（wire）格式**——
`coros_api` 本體自己再把 `repeat`/`steps` 轉成 COROS 伺服器實際存的
`is_group`/`sets`/`group_id` 結構。讀回時（`fetch_workout()`），一個重複組是**一個
`is_group=True` 的容器**（重複次數存在 `sets` 欄位），其子步驟**只列一次、不會依重複
次數物理展開**——`verify()` 比對時要對齊這個「容器 sets 值」而非「展開後的步驟數」。

時間型步驟：送出去是 `target_type: "time"`；**讀回欄位是 `duration_seconds`，不是
`target_duration_seconds`**——Task 5 原本猜測讀回也叫 `target_duration_seconds`，這是
一個真實的 bug，Task 7 對真帳號驗證後修正。

### Garmin

`RepeatGroupDTO`/`numberOfIterations`/巢狀 `workoutSteps` 的形狀，經確認**跟公開的
`garminconnect` 套件自帶的 `create_repeat_group()` helper 完全一致**——`garmin_runner.py`
直接呼叫該 helper（`from garminconnect.workout import create_repeat_group`），不用自己組
這層結構。讀回時，一個重複組是 `type == "RepeatGroupDTO"`、`numberOfIterations == N`，
內含 `workoutSteps` 陣列，陣列內是**未依重複次數展開的單一份**子步驟（每個子步驟
`type == "ExecutableStepDTO"`）——跟 COROS 側「容器 + 未展開子步驟」的形狀是同一種模式。
巢狀在重複組內的步驟，配速目標（pace-zone target，`workoutTargetTypeId == 6`）套用方式
跟頂層步驟完全相同，不需要特殊處理。

在探測過程中，另外發現 `garmin_runner.py` 引用的 `garminconnect.workout.TargetType.PACE_ZONE`
在該套件任何公開版本裡都不存在（只有 `NO_TARGET`/`POWER`/`CADENCE`/`HEART_RATE`/`SPEED`/
`OPEN`）——這是這次功能開發之前就存在、與間歇/重複組本身無關的既有 bug，代表 Garmin
同步路徑在真實安裝下此前其實從未能被成功 import。已改用既有驗證過的整數常數
`GARMIN_PACE_ZONE_TARGET_TYPE_ID = 6` 取代，Task 8 修正。

原規劃裡提到要另外確認 `garminconnect.workout.ConditionType` 底下代表「時間」的成員叫
什麼名字：確認結果是 `ConditionType.TIME`（與 `ConditionType.DISTANCE` 對稱存在，猜測
成立），`garmin_runner.py` 的 `_TIME_END_CONDITION`/`_DISTANCE_END_CONDITION` 已直接使用。

## 驗證（`verify()` 擴充）

沿用兩邊現有 `verify()` 的精神，不因為是新功能就跳過：

- 步驟數比對：**兩平台讀回的「原始 wire 形狀」一致（容器 + 只列一次的未展開子步驟，
  見上方「平台對應」小節），但兩邊 `verify()` 實際採用的比對粒度並不相同**，不可
  一概而論：
  - **COROS**（`coros_workout_plan.py` 的 `verify()`）：**不展開**，直接對齊容器層級——
    `_flatten_expected_steps()` 只把 `expected_steps` 裡的 repeat 節點拆成「一份子步驟
    +（另外記錄的）預期 repeat 次數」，之後拿這個次數去跟讀回的 `is_group=True` 容器
    的 `sets` 欄位比對，容器本身被排除在逐步驟比對之外（`exercises = [e for e in
    all_actual if not e.get("is_group")]`）。
  - **Garmin**（`garmin_workout_plan.py` 的 `verify()`）：**兩側都展開**——
    `_flatten_steps()`（讀回側）對每個 `RepeatGroupDTO` 執行
    `for _ in range(iterations): steps.extend(inner)`，`_flatten_expected_steps()`
    （預期側）執行 `step["steps"] * step["repeat_count"]`，展開成
    `repeat_count × len(steps)` 個步驟後逐步驟比對長度與內容。原規劃猜測的「展開成
    `repeat_count × len(steps)` 個實際步驟」這個做法，**在 Garmin 這邊是對的**，只是
    COROS 那邊沒有採用同一種做法——兩邊各自選了跟自己讀回資料形狀比較好比對的粒度，
    不是刻意求一致。
- 時間型分段：比照現有距離比對，改成比對 duration 欄位（誤差容忍值另訂，仿照
  `DISTANCE_TOLERANCE_M` 的模式）
- 配速比對邏輯不變，沿用現有的塌陷成 preset 預設值的指紋檢查

## CLI 整合

不新增 CLI 入口。同一份 `doc/YYYY-WNN-training-plan.md` 檔案裡，平坦列和重複組區塊可以
在同一份文件裡混用，`create_workouts.py --plan <file>` 與 `create_garmin_workouts.py
--plan <file>` 的呼叫方式完全不變——這樣操作者永遠只有「跑同一個指令去推這週的課表」
一種心智模型，不用因為某天是不是間歇課而換工具。

## 測試

- `tests/test_coros_workout_plan.py`：新增時間型分段解析、重複組區塊解析（含「錨點橫跨
  多片段」的邊界情況）、`build_steps()` 對 `RepeatGroup` 的轉換
- `tests/test_garmin_workout_plan.py`：對稱補上同樣的案例
- 現有全部既有測試（68 個）必須維持綠燈不變——沿用同一欄位自動判斷格式的設計，理論上
  對純距離型課表零影響，但要實測確認

## 首次上線驗證步驟（非自動化，實作計畫裡要包含這一步）

1. 完成上方「平台對應」的真帳號探測、`build_steps()`/`verify()` 分組邏輯定案
2. 用這週三（09-02）的真實課表，`create_workouts.py --dry-run` 先確認解析結果符合預期
3. 正式（非 dry-run）推上 COROS，讀回驗證通過
4. 用同一份課表跑 `create_garmin_workouts.py`，正式推上 Garmin，讀回驗證通過
5. 兩邊都驗證通過才算這個功能完成——這一步同時解決了這週三卡住沒推上去的真實待辦

## 已知限制（沿革，原樣繼承）

- 不支援巢狀重複組
- 不支援「連續跑中穿插短暫加速」構造（如週五 10km+6×20s）
- Garmin 重跑課表會產生重複項目的既有缺陷，這次不修
