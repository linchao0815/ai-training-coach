---
name: coros-sync
description: 把訓練計畫文件（doc/YYYY-WNN-training-plan.md 的 <!-- coros: ... --> 課表）建立並排上 COROS Training Hub 行事曆。使用時機：週課表已確認（同 Garmin 那份），需要推上 COROS 時。
---

# COROS 課表同步

跟 Garmin 共用同一份訓練計畫文件與同一個 `<!-- coros: day=YYYY-MM-DD name=... -->`
錨點——表格內容（名稱/距離-或-時間/配速）本來就跟平台無關，兩邊用同一個解析器
`coros_workout_plan.py`，語法完全一致。Garmin 那邊見 `garmin-sync` skill。

## 使用方式

1. 確認 `doc/YYYY-WNN-training-plan.md` 已經有 `<!-- coros: ... -->` 錨點與分段表格
   （通常這份文件在規劃時就已建立，見 `marathon-coach` skill 的流程）。
2. 先 `--dry-run` 檢查解析結果：
   ```bash
   python create_workouts.py --plan doc/YYYY-WNN-training-plan.md --dry-run
   ```
3. **這一步會真的建立並排上使用者的 COROS 行事曆，執行前務必先讓使用者看過
   `--dry-run` 的輸出並確認**（排程動作會改變使用者真實的訓練行事曆）：
   ```bash
   python create_workouts.py --plan doc/YYYY-WNN-training-plan.md
   ```
4. 每筆課表建立後會自動讀回驗證（配速、兩個顯示單位、估計時長），驗證沒過的
   課表會被刪除、不會排上行事曆，並印出具體哪裡不符——不要在有錯誤訊息的情況下
   跟使用者回報「已同步成功」。
5. **同日同名的舊排程會自動找到並移除**（`coros_runner.py` 的 dedup 邏輯：先記下
   舊排程 id，新課表建立、驗證、排程都成功後才刪舊的，任何一步失敗舊排程都不受
   影響）——這跟 Garmin 不同，Garmin 目前沒有這個機制，見 `garmin-sync` skill
   的已知限制。

## 已知限制

- 支援距離型與時間型分段、單層重複組（`>>> 重複 N 組 ... <<<` 語法）；不支援巢狀
  重複組、不支援「連續跑中穿插短暫加速」這種構造（例如「10km easy + 6×20s加速」——
  加速段的恢復是「繼續同一段跑」而非獨立一列，跟重複組形狀不同）。時間型分段沿用
  現有「距離」欄位、不新增表頭，`15分鐘`/`90秒` 這種格式會自動判斷成時間型，跟
  `8km` 這種距離型並存於同一欄。範例（`doc/2026-W36-training-plan.md` 09-02
  閾值課，對應到 `tests/test_coros_workout_plan.py` 的
  `TestParsePlanWithRepeatBlock`）：
  ```
  <!-- coros: day=2026-09-02 name=閾值課 -->
  | 名稱 | 距離 | 配速 |
  |---|---|---|
  | 熱身 | 15分鐘 | 5:45-6:00/km |
  >>> 重複 5 組
  | 間歇 | 4分鐘 | 4:15-4:25/km |
  | 組間 | 90秒 | 6:30-7:00/km |
  <<<
  | 收操 | 10分鐘 | 5:45-6:00/km |
  ```
  一個錨點可以橫跨多個表格片段（平坦表格與重複組區塊交錯出現）。配速欄只能放
  純配速（`5:40-5:55/km` 或 `4:45/km`），不能夾帶其他條件——`5:40-5:55/km，
  HR ≤145` 這種寫法會解析失敗（`clean_cell` 只剝掉粗體與括號附註，不處理逗號
  併列的條件）。HR 上限或其他條件要寫成表格下方的散文，不要塞進配速欄。
- 只處理跑步課表；重訓/騎車/游泳等既有課表庫項目仍要用 `mcp__coros__*` 工具
  （`schedule_workout`/`move_scheduled_workout` 等）排程——這支腳本只覆蓋新建
  的跑步課表。
- **認證**：需要先裝好 `coros-training-mcp`（`uv tool install coros-training-mcp`）
  並跑過 `coros-mcp auth`；也可以把 `COROS_EMAIL`/`COROS_PASSWORD` 放進專案根目錄
  的 `.env`（已 gitignore），`coros_runner.py` 的 `_load_dotenv_defaults()` 會自動
  讀取，見 README.md。
  - ⚠️ **`.env`/環境變數這條自動登入路徑一定要另外設 `COROS_REGION`**——沒設會
    預設打 `eu` 區域伺服器，登入 API 本身會正常回傳 200 與正確帳號資料，但後續
    所有查詢（包括這支腳本的讀回驗證）都會回「Access token is invalid」，非常
    容易誤判成帳號或 token 問題。這個帳號的正確值是 `COROS_REGION=asia`，完整
    診斷記錄見 README.md。
- `mcp__coros__*` 工具要 `.mcp.json` 正確設定（裸指令 `coros-mcp`，不要寫死絕對
  路徑）才會出現在 session 工具清單裡；改動 `.mcp.json` 後需要重啟 Claude Code
  才生效。`coros-mcp auth-status` 可以檢查 token 有效性（web token 約 24 小時
  過期）。

## 跟 marathon-coach / garmin-sync 的分工

`marathon-coach` 負責規劃並讓使用者確認週課表文件；課表確認後，COROS 用這個
skill、Garmin 用 `garmin-sync`——兩者是同一份確認結果的兩個推送目的地，互不依賴，
可以只推一邊，也可以兩邊都推。

## 技術細節

存在的理由（`pace` 參數會靜默塌成 preset 預設值、兩個 display_unit 欄位各自
預設錯等真實踩過的坑）見 `docs/superpowers/specs/2026-08-02-coros-workout-creation-design.md`
與 `doc/coros-estimated-time-bug.md`。目前實際檢查哪些項目，權威來源是
`coros_workout_plan.py` 的 `verify()`（或 `tests/test_coros_workout_plan.py` 的
`TestVerify`），不要相信任何散文清單——這份文件本身也刻意不重複列舉。各類檢查
防什麼，見 `wiki/concepts/coros-unit-pitfall.md`。

重複組建立走純函式 `coros_workout_plan.build_steps()` 產生
`{"kind": "repeat", "repeat": N, "steps": [...]}` 這個中介格式（`coros_api.create_run_workout()`
的輸入合約，不是 COROS 讀回的 wire 格式——讀回時一個重複組是一個 `is_group=True`
的容器、重複次數存在 `sets` 欄位，子步驟只列一次不物理展開）。時間型步驟送出
`target_type: "time"`，讀回欄位是 `duration_seconds`（不是 `target_duration_seconds`）。
這些欄位形狀已對真帳號驗證過，詳見
`docs/superpowers/specs/2026-09-02-interval-repeat-workout-design.md`。
