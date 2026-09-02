# COROS 配速/預估時間異常值 — 根本原因診斷（已解決）

## 結論（2026-07-17 確認修復）

**根本原因不是 COROS 伺服器的 bug，是本機 `coros-training-mcp` 工具自己的 bug。**

`pace_parser.py` 的 `parse_pace()`（被 `create_run_workout` 的 `pace` 參數呼叫）把配速算成「毫秒/公里」，並標記 `intensity_display_unit=2`。但比對使用者「上一個16週訓練block」裡、COROS原生介面建立的正常課表（例如「混氧訓練」「比賽強度訓練」）後發現，COROS 實際預期的格式是：

- `intensity_value` / `intensity_value_extend`：**純秒/公里**（不是毫秒），例如 5:12/km 存成 `312`，不是 `312000`
- `intensity_display_unit`：**1**（不是 2）

只要改用正確格式重新建立課表，`estimated_time_seconds` 立刻恢復正常（不再是1000倍），App/網站上的配速顯示也應該會正常。下面記錄完整的排查過程，保留給以後參考。

## 現象（最初觀察，2026-07-17上午）

透過 COROS MCP（`create_run_workout`）建立下週訓練課表後，用 `list_scheduled_workouts` 讀回時，凡是「距離＋配速」型的跑步步驟，其 `estimated_time_seconds` 欄位都出現天文數字：

| 課表 | 距離 | 錯誤的 `estimated_time_seconds` | 顯示成 |
|---|---|---|---|
| 輕鬆跑 10km | 10 km | 3,375,000 | 937:30:00（937.5 小時！） |
| 輕鬆跑 9km | 9 km | 3,037,500 | 843:45:00 |
| 8km + 6×20s 加速 | 8 km | 2,700,480 | 750:08:00 |
| 長距離跑 20km | 20 km | 6,800,000 | 1888:53:20 |

而「時間型」（非距離型）步驟完全正常（例如強度課的 `estimated_time_seconds=2850`→47:30），因為那些步驟本來就是使用者直接填秒數，不需要COROS用配速去推算距離對應的時間。

當時的初步假設是「COROS伺服器內部用毫秒算完忘記除以1000」，並找到一個吻合的驗證方式：把異常值除以1000，換算出的秒數都合理（10km→3375秒=56:15，20km→6800秒=1:53:20，剛好等於課表自己標注的「目標110-120min」）。**這個驗證方向是對的，但當時誤判成「COROS伺服器的bug」——後來才發現其實是我們自己寫入的 `intensity_value` 單位就存錯了（多乘了1000倍），COROS伺服器在需要用配速反推距離型步驟的預估時間時，直接使用了這個被錯誤放大1000倍的配速值去計算，才產生1000倍膨脹的預估時間。**

## 中間的排查彎路（App配速顯示異常）

使用者接著回報 App 上的「配速」數字本身也不對，例如手機顯示「8851"24/mi」，網站截圖顯示「28'51" - 32'53" min/mi」——兩個平台顯示**不同**的錯誤數字。這階段誤以為是COROS前端（App/網站)各自獨立的顯示bug（因為兩邊數字對不上同一個簡單換算公式），並建議使用者去檢查帳號的公制/英制顯示設定。

**這個中間結論後來也被修正**：兩邊顯示不同錯誤值，只是因為App和網站在處理「一個從一開始就是錯誤格式（單位放大1000倍、display_unit標錯）」的數值時，各自用不同的方式處理異常輸入，才產生不同的錯誤結果——根源仍然是同一個：我們寫入的資料格式本身就錯了。

## 真正的根本原因（確認過程）

比對使用者上一個訓練block（2026-07-06~07-12週）COROS原生建立、執行完全正常的課表資料，發現關鍵差異：

**COROS原生課表（正常）**：
```
intensity_value: 312          # 純秒/公里 = 5:12/km
intensity_display_unit: 1
estimated_time_seconds: 7193  # 正常數字（119.9分鐘）
```

**我們工具建立的課表（異常）**：
```
intensity_value: 330000       # 毫秒/公里 = 5:30/km，但單位錯了1000倍
intensity_display_unit: 2     # 也跟原生格式不同
estimated_time_seconds: 3375000  # 千倍膨脹
```

修正後（改用 `intensity_value` 直接填秒數、`intensity_display_unit=1`）重新建立下週5堂跑步課表，`estimated_time_seconds` 全部恢復正常：

| 課表 | 修正前 | 修正後 |
|---|---|---|
| 週二輕鬆跑10km | 3,375,000 | 3,375（56:15） |
| 週四輕鬆跑9km | 3,037,500 | 3,038（50:38） |
| 週六8km+加速 | 2,700,480 | 3,180（53:00） |
| 週日長跑20km | 6,800,000 | 6,800（1:53:20） |

## 影響範圍與修正方式

- **受影響**：所有透過 `create_run_workout` 的 `pace` 便利參數（或手動指定 `intensity_display_unit=2` + 毫秒值）建立的「距離＋配速」型跑步步驟。時間型步驟（`target_type=time`）不受影響，因為預估時間不需要用配速反推。
- **修正方式**：呼叫 `create_run_workout` 時**不要用 `pace` 字串參數**（它內部呼叫有bug的 `pace_parser.py`），改成直接指定原始欄位：
  ```
  intensity_type: 3
  intensity_value: <純秒/公里，例如 5:30/km = 330>
  intensity_value_extend: <純秒/公里，例如 5:45/km = 345>
  intensity_display_unit: 1
  hr_type: 0
  is_intensity_percent: false
  ```
- 若要回報上游：`coros-training-mcp` 的 `pace_parser.py`（`parse_pace()` 函式）需要修正，把回傳的 `intensity_value`/`intensity_value_extend` 除以1000（秒制而非毫秒制），並把 `intensity_display_unit` 從 `2` 改成 `1`。目前 GitHub issue tracker 沒人回報過這個問題。

## 追加發現（2026-07-17）：距離顯示單位也是同一個模式的bug（顯示成英里）

修好配速之後，使用者回報「總距離都變成mi」。檢查後發現：距離型步驟的 `target_display_unit` 預設值也被本機工具設成 `3`，而COROS原生課表（同一批上一週期的正常課表）用的是 `1`——跟 `intensity_display_unit` 的2 vs 1是同一個模式的bug，只是換了一個欄位。

**修正方式**：跟配速一樣，建立距離型步驟時要明確指定 `target_display_unit: 1`，不要用工具的預設值。修正後連top-level的 `distance_display_unit` 也會一併從 `3` 變回 `1`（看起來top-level欄位是從step-level設定推導出來的，不需要另外處理）。

至此，`create_run_workout` 建立跑步課表時，每個距離+配速步驟都需要明確指定以下欄位（不要依賴任何預設值）：
```
target_type: distance
target_distance_meters: <公尺>
target_display_unit: 1        # 距離用公里顯示
intensity_type: 3
intensity_value: <純秒/公里>
intensity_value_extend: <純秒/公里>
intensity_display_unit: 1      # 配速用公里顯示
hr_type: 0
is_intensity_percent: false
```

## 另外發現的獨立bug：`update_run_workout` / `replace_scheduled_workout` 對「已排程」課表會壞掉

嘗試用 `replace_scheduled_workout` 修改一個已經排入行事曆的課表時，回傳的複製結果完全跑掉——變成一個帶有3個步驟、25km距離、來源不明的預設範本課表，而不是原本單一步驟的10km課表。

原因推測：`get_workout`（`/training/program/query`）查不到已經被排入行事曆的課表ID（回傳"not found"，只能查到「還在課表庫、尚未排程消耗」的項目）。`update_run_workout`/`replace_scheduled_workout` 內部依賴同一個查詢去抓「原始課表」來複製+修改，抓不到就會fallback成某個預設範本（結構跟 `get_workout_builder_catalog` 回傳的 `baseline_first_exercise` 完全一致），而不是報錯。

**結論：對於「已經排程」的課表，不要用 `update_run_workout`/`replace_scheduled_workout` 去修改。** 正確做法：
1. `remove_scheduled_workout` 移除舊的排程項目
2. `create_run_workout`（全新建立，不依賴抓取舊課表）建立修正後的課表
3. `schedule_workout` 排回原本的日期
