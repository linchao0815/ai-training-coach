---
name: garmin-sync
description: 把訓練計畫文件（doc/YYYY-WNN-training-plan.md 的 <!-- coros: ... --> 課表）推上並排程到 Garmin Connect 行事曆。使用時機：課表已確認（同 COROS 那份），需要同步到 Garmin 裝置時。
---

# Garmin 課表同步

跟 COROS 共用同一份訓練計畫文件與同一個 `<!-- coros: day=YYYY-MM-DD name=... -->`
錨點——表格內容（名稱/距離/配速）本來就跟平台無關，不需要為 Garmin 另外寫一份。

## 使用方式

1. 確認 `doc/YYYY-WNN-training-plan.md` 已經有 `<!-- coros: ... -->` 錨點與分段表格
   （通常這份文件已經因為要推 COROS 而存在，見 `marathon-coach` skill 的流程）。
2. 先 `--dry-run` 檢查解析結果：
   ```bash
   python create_garmin_workouts.py --plan doc/YYYY-WNN-training-plan.md --dry-run
   ```
3. **這一步會真的建立並排上使用者的 Garmin Connect 行事曆，執行前務必先讓使用者
   看過 `--dry-run` 的輸出並確認**（跟 COROS 那邊 `create_workouts.py` 的規矩一致，
   排程動作會改變使用者真實的訓練行事曆）：
   ```bash
   uv run create_garmin_workouts.py --plan doc/YYYY-WNN-training-plan.md
   ```
4. 每筆課表建立後會自動讀回驗證（步驟數、距離、配速換算、預估時長），驗證沒過的
   課表會被刪除、不會排上行事曆，並印出具體哪裡不符——不要在有錯誤訊息的情況下
   跟使用者回報「已同步成功」。

## 已知限制

- 支援距離型與時間型分段、單層重複組（`>>> 重複 N 組 ... <<<` 語法）；不支援巢狀
  重複組、不支援「連續跑中穿插短暫加速」這種構造（例如「10km easy + 6×20s加速」——
  加速段的恢復是「繼續同一段跑」而非獨立一列，跟重複組形狀不同）。時間型分段沿用
  現有「距離」欄位、不新增表頭，`15分鐘`/`90秒` 這種格式會自動判斷成時間型，跟
  `8km` 這種距離型並存於同一欄。跟 COROS 那邊 `coros_workout_plan.py` 共用同一個
  解析器，兩平台語法完全一致。範例（`doc/2026-W36-training-plan.md` 09-02 閾值課，
  對應到 `tests/test_coros_workout_plan.py` 的 `TestParsePlanWithRepeatBlock`）：
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
  一個錨點可以橫跨多個表格片段（平坦表格與重複組區塊交錯出現），上面例子就是
  「一段平坦表格 → 一個重複組 → 一段平坦表格」共三段接在同一個錨點下。
- 只處理跑步課表，不含重訓/騎車/游泳。
- 認證沿用 `GARMIN_EMAIL`/`GARMIN_PASSWORD` 環境變數 + `~/.garminconnect/`
  session cache，跟 `fetch_garmin_data.py` 共用（見 README.md）；這兩個變數也可以
  放在 `garmin_auth.py` 同目錄下的 `.env` 檔案（已加入 .gitignore）取代在 shell
  裡 export。
- 目前沒有像 COROS 那邊的『同日同名自動移除舊排程』邏輯——重複執行
  `create_garmin_workouts.py` 對同一份計畫檔會在 Garmin Connect 上建立重複的
  課表，不會覆蓋掉舊的。需要重新推送時，先手動到 Garmin Connect 刪除當天舊的
  課表。**這不是理論風險**：2026-09-02 正式推送時，同一份計畫檔裡有兩天
  （09-05、09-06）先前就已經推過 Garmin，重跑整份檔案讓這兩天各自被複製成
  兩筆排程，靠 `client.get_scheduled_workouts()` 查出來手動刪除多的那筆才
  清乾淨。**只想推新增/修改的那幾天時，不要對整份計畫檔重跑**，除非確定
  其他天還沒推過。

## 跟 marathon-coach / coros-sync 的分工

`marathon-coach` 負責規劃並讓使用者確認週課表文件；課表確認後，COROS 用
`coros-sync` skill、Garmin 用這個 skill——兩者是同一份確認結果的兩個推送目的地，
互不依賴，可以只推一邊，也可以兩邊都推。

## 技術細節

見 `docs/superpowers/specs/2026-09-01-garmin-workout-sync-design.md`（一般欄位設計）
與 `docs/superpowers/specs/2026-09-02-interval-repeat-workout-design.md`（間歇/重複組
設計）、`garmin_workout_plan.py`/`garmin_runner.py` 的原始碼與註解（配速欄位換算方向、
`RepeatGroupDTO`/`numberOfIterations` 讀回格式的確認記錄）。

重複組建立走 `garminconnect` 套件自帶的 `create_repeat_group()` helper（`garmin_runner.py`
的 `_build_repeat_group()`），已對真帳號確認巢狀在重複組內的步驟配速目標
（pace-zone target）也能正確套用，不需要對「在組內 vs 組外」特殊處理。另外，
`garmin_runner.py` 曾誤引用不存在的 `garminconnect.workout.TargetType.PACE_ZONE`
（該套件公開版本從未有這個成員），導致 Garmin 同步路徑在真實安裝下其實從未能被
import／執行過；這個問題已在這次功能開發過程中修正（改用既有驗證過的整數常數
`GARMIN_PACE_ZONE_TARGET_TYPE_ID`），現在可以正常運作。
