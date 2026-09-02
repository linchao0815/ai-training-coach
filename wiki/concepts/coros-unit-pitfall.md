---
title: COROS 單位陷阱（ms vs s / display_unit）
type: concept
updated: 2026-08-16
sources: [doc/coros-estimated-time-bug.md, doc/example-training-plan.md, .claude/skills/marathon-coach/SKILL.md, .mcp.json, coros-mcp即時操作]
---

# COROS 單位陷阱

## 結論
**根本原因不是 COROS 伺服器的 bug，是本機 `coros-training-mcp` 工具自己的 bug。**

`pace_parser.py` 的 `parse_pace()`（被 `create_run_workout` 的 `pace` 參數呼叫）把配速算成「毫秒/公里」，並標記 `intensity_display_unit=2`。但 COROS 實際預期：

- `intensity_value` / `intensity_value_extend`：**純秒/公里**（不是毫秒）。例：5:12/km 應存成 `312`，不是 `312000`。
- `intensity_display_unit`：**1**（公里），不是 2。
- 距離型步驟同理，`target_display_unit` 也要明確指定為 **1**（公里），否則距離顯示單位也會錯。

只要用正確格式建立課表，`estimated_time_seconds` 立刻恢復正常（不再暴衝 1000 倍），App/網站配速顯示也正常。

## 症狀（診斷用，供未來重現時比對）
- 「距離＋配速」型步驟的 `estimated_time_seconds` 出現天文數字（例：10km 課表顯示 3,375,000 秒＝937.5 小時）；異常值除以 1000 後換算出的時間剛好合理 —— 這是判斷「多乘 1000」的關鍵訊號。
- 「時間型」步驟不受影響（使用者直接填秒數，不需要 COROS 用配速反推距離）。
- App 與網站顯示的錯誤配速數字**彼此不同**（例：App「8851"24/mi」vs 網站「28'51"-32'53" min/mi」）——這是兩邊各自用不同方式處理**同一個根本已錯的輸入**，不是兩個獨立的顯示 bug，不要被這個表象誤導去查帳號公制/英制設定。

## 排查彎路（記錄下來避免重蹈）
1. 一開始誤判「COROS 伺服器內部用毫秒算完忘記除以1000」——這個驗證方向（除以1000還原）是對的，但**歸咎對象錯了**：真正錯的是我們自己寫入的 `intensity_value`。
2. App/網站顯示不同錯誤值時，一度誤以為是前端各自獨立的顯示 bug，建議去查公制/英制設定——後來確認是同一根源的下游不同呈現方式。

## 跨平台佐證
`doc/references.md` 記錄同類型問題在 Zwift/TrainingPeaks 論壇也常見：距離型步驟的欄位被誤讀成時間型。共同根因：**「距離型」與「時間型」步驟共用同一組欄位，卻沒有依 target 型態做單位驗證**。中立參考模型可見 [Structured Workout Format](https://structuredworkoutformat.dev/)（時長統一用秒）。

## 應用現況
`doc/2026-W30-training-plan.md` 已確認用正確格式（`intensity_display_unit`/`target_display_unit`=1，`intensity_value`=純秒/公里）建立，不再出現異常。

> ⚠️ 這代表的是「**手寫 raw 欄位**這條路可行」，**不是**工具已修好。`pace` 參數 2026-08-02 實測仍然是壞的，見下方專節。

## 讀取端的同類陷阱：`get_activity_detail` 的 `avgSpeed`/`avgPace`（2026-07-26發現）
上面記錄的是**寫入端**（`create_run_workout`）的毫秒/秒誤植；這裡是**讀取端**（`get_activity_detail`）另一個獨立但同類的陷阱：`summary.avgSpeed`、`lapItemList[].avgPace`欄位**命名是速度，實際存值卻是純秒/公里**（例：364.53 對應 6:05/km，不是364.53公分/秒的速度）。曾在回顧07-25/07-26兩筆跑步紀錄時，把這個值誤當cm/s速度換算成4:34/km、4:38/km，被使用者用手動記錄的真實配速（6:05/km、5:59/km）當場糾正——正確算法是直接把數值當「秒/公里」讀（364.53秒=6分04.5秒/km）。**共同教訓**：COROS的配速/速度類欄位命名不可信，每次遇到新欄位都要先用已知真實配速反推驗證單位，不要預設命名代表的量綱。

## 🚨 `pace` 參數在 v0.2.0 仍然是壞的（2026-08-02 實測復現）

上面「應用現況」說 W30 已用正確格式建立、不再出現異常——**但那是因為當時手寫 raw 欄位，不是因為工具修好了**。2026-08-02 建立 W32 週日長跑時改用 `create_run_workout` 的 `pace` 人類可讀字串（`"4:40-4:50/km"` 等四段不同配速）＋ `intensity_label: "Pace"`，結果：

| 步驟 | 指定配速 | 實際寫入 `intensity_value`/`_extend` |
|---|---|---|
| 熱身 8km | 5:40-5:55/km | 186411 / 223694 |
| Easy 6km | 5:30-5:45/km | 186411 / 223694 |
| 馬配段 4km | 4:40-4:50/km | 186411 / 223694 |
| 收操 6km | 5:40-6:00/km | 186411 / 223694 |

四段**全部塌成同一組值**，而且正是 `get_run_workout_schema` 裡 `Pace` preset 的預設值（186411 ms/km ≈ 3:06/km、223694 ≈ 3:44/km）。API 回傳成功、沒有任何錯誤或警告。若沒讀回來檢查，使用者會在錶上看到一堂 24km 輕鬆長跑標著 3:06/km。

**正確做法**：不要用 `pace` 參數，直接寫 raw 欄位、單位用**純秒/公里**、`intensity_display_unit: 1`：

```
{intensity_type: 3, is_intensity_percent: false,
 intensity_value: 280, intensity_value_extend: 290,   // 4:40-4:50/km
 intensity_display_unit: 1}
```

### 兩個 display_unit 欄位，兩個都會預設錯（2026-08-02 第二次踩到）

每個步驟有**兩個獨立的顯示單位欄位**，且都不會自動跟隨：

| 欄位 | 管什麼 | 錯的後果 |
|---|---|---|
| `intensity_display_unit` | 配速單位 | 配速數值被當毫秒解讀 |
| `target_display_unit` | **距離單位** | 距離在錶/App 上顯示成**英里** |

本頁「結論」一節早就寫明 `target_display_unit` 也要設 1——但 2026-08-02 修配速那一輪**只設了 `intensity_display_unit`**，結果配速對了、四段距離全部顯示成英里，而課表標題寫的是公里，是使用者發現「分段距離變成英里」才抓到。**兩個都要在每個距離型步驟上明確指定為 1**；課表層級的 `distance_display_unit` 會自動跟隨步驟，不需另外設。

### 讀回驗證：權威清單在程式裡，不在這一頁

**建立課表後必須讀回驗證**。2026-08-02 起這件事已機械化：`create_workouts.py` 自動執行檢查，任一項不過就刪掉剛建立的課表並拒絕排程（行事曆不會被碰）。手工組 MCP 呼叫建立新跑步課表的作法已停用，理由見 `docs/superpowers/specs/2026-08-02-coros-workout-creation-design.md`。

> **權威清單是 `coros_workout_plan.py` 的 `verify()` 函式**，不是這一頁。要知道目前實際檢查哪些項目，去讀那支函式或 `tests/test_coros_workout_plan.py` 的 `TestVerify`。
>
> ⚠️ **本頁刻意不列舉當下的檢查項目**。初版曾列出「四項，缺一不可」，結果同一天最終 review 就加了第五項（儲存距離 `target_value == 公尺 × 100`），這一頁隨即變成假的。**程式會長，散文不會**——複述一份會成長的清單，等於保證它遲早說謊。

**每一類檢查為什麼存在**（這才是這一頁該記的，因為它不會隨實作變動）：

| 類別 | 防的是什麼 | 來源事故 |
|---|---|---|
| 配速數值逐步比對，且**指定不同配速的步驟之間數值必須相異** | `pace` 參數把所有步驟塌成 preset 預設值 | 2026-08-02 事故一 |
| 兩個 display_unit 各自檢查 | `target_display_unit` 預設 3（英里）、`intensity_display_unit` 預設 0 | 2026-08-02 事故二 |
| 儲存距離與送出距離比對 | 距離存錯只會被估計時長間接、寬鬆地抓到 | 2026-08-02 最終 review |
| `estimated_time_seconds` 量級 | 配速編碼錯誤的獨立佐證（見下節） | — |

**新增一類陷阱時的正確做法**：加進 `verify()` 並補測試，然後在上表補一列說明「防什麼」。**不要**在這裡列出實作細節——那是程式的工作。

> 💡 判斷一條規則有沒有防線的通則：問「它被違反的當下，什麼東西會變紅？」如果答案是「有人記得去看」，那它沒有防線。英里那個 bug 的規則當時已經寫在本頁「結論」一節且是粗體，修正時還引用了本頁，依然漏掉——**因為當時的驗證步驟只檢查配速**。

修復已排程的課表要三步：`update_run_workout`（`delete_original: true` ＋ raw 欄位）→ `remove_scheduled_workout` → `schedule_workout`。**行事曆存的是課表的複本**，只改課表庫不會更新已排程的那筆。

### 附帶修正：`estimated_time_seconds` 1000 倍是下游症狀，不是獨立 bug
同一份課表，ms/km 版本回傳 `estimated_time_seconds: 4921260`；改寫成秒/km ＋ `display_unit: 1` 後回傳 **8045**（2:14:05，正確）。所以**合理的 estimated_time 反而是配速編碼正確的正面訊號**，異常值代表配速欄位本身錯了、不只是顯示錯。這修正了本頁與 `doc/coros-estimated-time-bug.md` 原本「COROS 顯示會怪但配速有同步成功」的說法——配速並沒有同步成功。

## 讀取端陷阱：`elevGain` 只算爬升，不是地形指標（2026-08-02）

`lapItemList[].elevGain` 與 `summary.elevGain` **只累加上升**，不反映淨高度變化。拿它當「這公里是不是平路」的篩選條件會把**下坡段誤判成平路**——2026-08-02 的長跑裡，某個 `elevGain` 僅 14m 的公里實際淨高度是 **−63m**。

後果是一次錯誤的 gate 綠燈：前 5km（淨 **+64m 上坡**）vs 末 5km（淨 **−44m 下坡**）的比較顯示「末段步幅反而拉長 3.4cm」，被當成「後段沒有崩壞」的證據。實際上同一趟跑內步幅對淨坡度的相關性是 **r = −0.82**（斜率 −0.198 cm/每公尺），光地形就能解釋約 10cm——使用者當場指出「下坡」才發現。

**正確做法**：`淨高度差 = elevGain − totalDescent`（兩者都是 lap 層級欄位），用它配對地形。詳見 `.claude/skills/marathon-coach/SKILL.md` 的長跑 gate 一節。

## 讀取端陷阱：`weather` 區塊全部 ×10，缺值是 int32 sentinel（2026-08-02）

`detail_json.weather` 的 `temperature` 280 = 28.0°C、`bodyFeelTemp` 330 = 33.0°C、`humidity` 500 = 50.0%。看起來是**單一快照（接近活動起點）**，不是全程平均——2 小時以上的跑步，結束時的實際條件比記錄值熱得多，不要當成整趟的代表值。

缺值以 int32 最小值 **−2147483648** 表示（除以 10 後看到 −214748364.8，例：2026-06-21 的 `bodyFeelTemp`）。任何量級荒謬的值都應視為 null，不是資料。

## Garmin 年代（`data/garmin.db` + `data/garmin_detail/`）的同類陷阱（2026-08-16）

本頁原本只寫 COROS。原專案在把一份跨年度賽事配速分析頁（本範本未收錄，屬於個人資料）的總表接上機器對帳（`verify_wiki_numbers.py` 對應的 checker）時，在 Garmin 那半邊踩到三個**性質完全相同、但細節不同**的坑。**不要假設兩邊的欄位語意一致——它們幾乎沒有一項一致。**

| | COROS | Garmin |
|---|---|---|
| lap 資料位置 | `detail_json.lapList[0].lapItemList[]` | `detail_json.splits.lapDTOs[]` |
| lap 距離單位 | 公分（`distance` ÷ 100000 = km） | **公尺**（`distance` ÷ 1000 = km） |
| lap 時間單位 | 百分之一秒（`time` ÷ 100 = 秒） | **秒**（`duration`，欄位名也不同） |
| `start_time` | 正確的 epoch，用 `localtime` 即可 | **把當地時間當成 UTC 存**，需 **+8 小時**校正 |

1. **時間戳偏移 8 小時**。06:30 起跑的台北馬，用 `datetime(start_time,'unixepoch','localtime')` 讀出來是**前一天 22:30**。查詢要寫 `datetime(start_time,'unixepoch','localtime','+8 hours')`。這件事那頁開頭本來就記著「Garmin 資料因 UTC 儲存」，但**校正方向要實際試過才知道**——照字面理解可能會往反方向調。

2. **`activities.distance_meters` 常是四捨五入後的 42.2km，不是 GPS 距離**。2019 台北馬該欄位寫 42.2，但逐 lap 加總是 **43.53km**——而九場總表引用的正是後者。拿那個欄位對帳會九場全部誤判成「wiki 寫錯」。這跟 COROS 的 `avgSpeed` 其實是秒/公里、`elevGain` 只算爬升是**同一個家族**：欄位名稱／看起來合理的值，都不保證是你要的那個量。

3. 實作上用同一個 `_split_from_laps()` 處理兩邊，靠傳入兩個換算函式吸收單位差異，不要複製兩份半馬切分邏輯。

**通則（三次踩坑後的共同結論）**：讀任何配速/距離/時間欄位前，先拿**一個已知正確的值**反推驗證——問使用者、或跟同一活動另一個來源（錶上顯示、官方成績）對。這條規則在本頁已經出現第四次了（寫入端 `pace`、讀取端 `avgSpeed`、`elevGain`、Garmin 三連），把它當成預設流程而不是例外處理。

## 其他已知 MCP 限制
- `create_strength_workout` 的 `target_type` 參數只能傳整數（2=計時、3=次數），傳字串別名會被 COROS API 拒絕（見 `README.md` 已知限制）。
- `create_run_workout` 的 `pace` 參數不可用（見上）。
- `.mcp.json` 的 `command` 若寫死絕對路徑（原本是 Windows 的 `coros-mcp.exe`），在另一個平台上會**靜默失敗**：MCP server 起不來、`mcp__coros__*` 工具整組消失、沒有任何錯誤訊息。2026-08-02 已改成裸指令 `coros-mcp` 靠 PATH 解析。改動 `.mcp.json` 後**需重啟 Claude Code session** 才生效。
