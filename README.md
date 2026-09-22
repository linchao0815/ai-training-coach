# ai-training-coach

> **這是一份去個資化的範例專案**，改寫自作者的個人訓練資料同步/分析工具集，拿掉了所有真實的個人健康、傷病、訓練資料，只保留通用的工具程式碼、Claude Code skills、以及「LLM 維護的持久化知識庫」這套機制本身。想直接使用的話，複製 [my.md.example](my.md.example) 成 `my.md`（已加入 `.gitignore`）填入你自己的資料，再依下方步驟裝好 COROS/Garmin 認證即可。

個人 COROS/Garmin 訓練資料抓取、分析與課表管理工具集。搭配 `coros-training-mcp` MCP server（見 `.mcp.json`）操作 COROS Training Hub，並用一套由 LLM 維護的 wiki（`wiki/`）持續累積訓練/健康背景知識，供 `marathon-coach` skill 規劃每週訓練時參考。

## coros 帳號設定（新環境必做一次）

`coros-training-mcp` 不隨這個 repo 一起 clone，要另外裝；裝好之後才有 `coros-mcp` 指令可以認證。

```bash
uv tool install coros-training-mcp   # 若還沒有 uv：curl -LsSf https://astral.sh/uv/install.sh | sh
coros-mcp auth                       # 互動式登入，會問你帳號的 region（例如 asia / eu，依你的帳號實際註冊地區）
```

認證成功後 token 會存在本機（不在版控裡），`coros-mcp serve`（MCP server）與 `fetch_training_data.py` 都直接沿用同一份 token，不需要各自登入。token 過期或換機器時重跑一次 `coros-mcp auth` 即可。

**平台差異**：`.mcp.json` 的 `command` 用的是裸指令 `coros-mcp`（靠 PATH 解析），Windows 與 Linux/macOS 通用——若寫死絕對路徑，在別的平台上會讓 MCP server 靜默啟動失敗（Claude Code 只在 session 啟動時載入 MCP server，失敗時不會有明顯錯誤，症狀是 `mcp__coros__*` 工具整組不存在）。若改動 `.mcp.json`，**需要重啟 Claude Code session 才會生效**。

README 後面的腳本執行範例是 Windows 的 venv 路徑，Linux 上對應的是 `~/.local/share/uv/tools/coros-training-mcp/bin/python`。`fetch_training_data.py` 本身已同時處理兩邊的 venv 佈局（Windows 的 `Scripts/python.exe` 與 POSIX 的 `bin/python`），那支腳本不用改。

沒裝而直接跑 `coros-mcp auth` 的症狀就是 `指令找不到` / `無此指令`——那是「工具不存在」，不是參數打錯。

⚠️ **`COROS_REGION` 一定要設，不設會拿到「看起來登入成功、查詢卻全部失敗」的假象**：`coros_api.get_env_credentials()` 沒讀到 `COROS_REGION` 時預設是 `"eu"`（打 `teameuapi.coros.com`）。用錯區域時，登入 API 本身會正常回傳 200 與正確的帳號資料，只有後續的查詢類 API 才會全部回「Access token is invalid」——非常容易誤判成 token 過期或帳號問題，實際上是打錯區域伺服器。`.env` 裡務必依你帳號的實際地區設定（範例見 [.env.example](.env.example)）。

## git hook 安裝（clone 後必做一次）

```bash
git config core.hooksPath githooks
```

`githooks/post-commit` 會在每次 commit 後，比對 `wiki/WIKI.md` 的來源白名單，把動到的來源檔排進 `wiki/.pending-ingest`，供下次對話決定要不要 ingest 進 wiki。它不阻塞 commit、不自動花 token。commit 動到 `wiki/` 或 `data/` 時，它另外會跑一次 `verify_wiki_numbers.py`（同樣只報告、不阻塞）。

`githooks/post-merge` 做同一件事，但針對 **`git pull` 拉進來的變更**。白名單比對與寫佇列的實作在 `githooks/lib-queue.sh`，兩支 hook 共用同一份，避免「只加了一種資料來源、忘了對稱加另一種」的漏網。

**注意事項**：
- rebase 期間會自動跳過（rebase 會為每個重放的 commit 各觸發一次 post-commit，但那些內容早就 ingest 過）；`git cherry-pick` 刻意不跳過。
- `git pull --rebase` 走的是 `post-rewrite` 不是 `post-merge`，`git reset --hard <remote>` 完全不觸發任何 hook——這兩種情況佇列仍會漏，要手動確認。判斷 pull 進來的內容有沒有被 ingest，最可靠的是看 `wiki/log.md` 有沒有對應紀錄。
- **為什麼需要手動跑這一步**：git hook 不隨 `git clone` 複製，所以 hook 本體放在版控的 `githooks/` 目錄、用 `core.hooksPath` 指過去；但 `core.hooksPath` 本身是 local config，仍然不會跟著 clone。這一步漏掉的話會**靜默失效**——commit 照常成功，只是不再排隊，也沒有任何錯誤訊息。

驗證與排查：
```bash
git config --get core.hooksPath              # 應輸出 githooks（post-commit 與 post-merge 共用這個設定）
githooks/post-commit <commit> --dry-run      # 拿任一歷史 commit 試跑白名單比對，不寫入佇列
githooks/post-merge <base> <head> --dry-run  # 拿任一區間試跑，例如 pull 前後的兩個 sha
```

## 檔案

- **`my.md.example`** — 複製成 `my.md`（已 gitignore）並填入你自己的跑者背景（傷病史、目標、跑量、跑鞋系統），課表設計的核心依據。
- **`githooks/post-commit`** — wiki ingest 佇列的 commit hook（見上方安裝說明）。
- **`githooks/post-merge`** — 同上，但針對 `git pull` 拉進來的變更。
- **`githooks/lib-queue.sh`** — 兩支 hook 共用的白名單比對、寫佇列與數字對帳（不是 hook 本身，由 hook 載入）。
- **`fetch_training_data.py`** — 抓取活動列表、完整活動細節、每日生理指標、睡眠、課表庫、排定行事曆，寫進 `data/coros.db` + `data/detail/`。
- **`fetch_garmin_data.py`** — 補齊 COROS 之前的 Garmin Connect 歷史資料，一次性/低頻執行，寫進 `data/garmin.db` + `data/garmin_detail/`。
- **`analyze_training.py`** — 把 `data/coros.db` 的原始資料彙整成週/月摘要、長跑清單、強度課清單，輸出到 `data/analysis/`，同時鏡射回 `data/coros.db`。
- **`create_workouts.py`** — 從 `doc/YYYY-WNN-training-plan.md` 的 `<!-- coros: -->` 錨點建立跑步課表，讀回驗證通過後才排上行事曆（驗證項目以 `coros_workout_plan.py` 的 `verify()` 為準，各類檢查防什麼見 `wiki/concepts/coros-unit-pitfall.md`）。搭配純函式模組 `coros_workout_plan.py`（標準庫，測試在 `tests/`）與網路層 `coros_runner.py`。`--dry-run` 只印解析結果不連線。存在的理由見 `docs/superpowers/specs/2026-08-02-coros-workout-creation-design.md`。**認證**：預設沿用 `coros-mcp auth` 存好的 token；沒有 token 時會 fallback 到 `COROS_EMAIL`/`COROS_PASSWORD` 自動登入，這兩個變數也可以放在專案根目錄的 `.env` 檔案（已加入 .gitignore，跟 Garmin 那邊的 `.env` 同一份檔案即可，範例見 [.env.example](.env.example)）取代在 shell 裡 export，見 `coros_runner.py` 的 `_load_dotenv_defaults()`。
- **`garmin_workout_plan.py`** — 把 `coros_workout_plan.parse_plan()` 解析出的課表分段轉成 Garmin Connect 需要的步驟資料，並在建立課表後讀回驗證步驟數/距離/配速換算/預估時長（純函式模組，標準庫，不 import `garminconnect`，測試在 `tests/`）。跟 COROS 共用同一份 `<!-- coros: ... -->` 錨點與表格，不是另一套語法。
- **`garmin_auth.py`** — `fetch_garmin_data.py` 與 `garmin_runner.py` 共用的 Garmin Connect 認證邏輯（`GARMIN_EMAIL`/`GARMIN_PASSWORD` + `~/.garminconnect/` session cache；這兩個變數也可以放在 `garmin_auth.py` 同目錄下的 `.env` 檔案取代在 shell 裡 export）。
- **`garmin_runner.py`** — Garmin 課表同步的網路層：建立 → 讀回驗證 → 排程，搭配 `create_garmin_workouts.py` 使用。
- **`create_garmin_workouts.py`** — 把訓練計畫文件的課表推上並排程到 Garmin Connect 行事曆，用法跟 `create_workouts.py` 對稱（`--dry-run` 先看解析結果）。存在的理由與配速欄位換算的確認記錄見 `docs/superpowers/specs/2026-09-01-garmin-workout-sync-design.md`。也可以透過 `.claude/skills/garmin-sync/SKILL.md` 這個 skill 使用。**支援時間型分段與單層重複組**（`>>> 重複 N 組 ... <<<` 語法，`15分鐘`/`90秒` 這種格式沿用既有「距離」欄自動判斷成時間型，不新增表頭），跟 COROS 共用同一個解析器（`coros_workout_plan.py`）與同一份 markdown 語法，具體語法範例見 `tests/test_coros_workout_plan.py` 與 `doc/example-training-plan.md`；不支援巢狀重複組，設計細節見 `docs/superpowers/specs/2026-09-02-interval-repeat-workout-design.md`。**「連續跑中穿插短暫加速」（例如「10km easy + 6×20s加速」）其實不需要新語法**：只要恢復段配速有明確目標帶，形狀跟其他重複組完全一樣（Easy 熱身 → 重複 N 組 [加速／恢復] → Easy 收操），直接用既有語法寫就行。
- **`garmin_strength_plan.py`** — 建 Garmin 肌力課表的純函式模組（標準庫，不 import `garminconnect`，測試在 `tests/test_garmin_strength_plan.py`）。`garminconnect[workout]` 完全沒有肌力課表的 typed model（讀套件原始碼確認：`SportType` 只有跑/騎/游/走/健行/多項運動/健身器材/其他，沒有肌力），所以這裡直接手組 Garmin 的原始 JSON schema，透過 `garminconnect.Garmin.upload_workout()`（接受原始 dict，跳過 typed model 那層）送出——只需要 `garminconnect`，不需要 `[workout]` extra。動作對應表可查社群反推文件 [n1t3k/garmin-strength-api](https://github.com/n1t3k/garmin-strength-api)，或自己在 Garmin App 手動建一堂課、用 `client.get_workout_by_id()` 讀回來看實際存了什麼。已知陷阱：`exerciseName` 沒對到 Garmin 動作字典裡的名稱會被**靜默清空**（不報錯，其他欄位仍正確）；`category` 沒對到合法分類則整包上傳 400。
- **`garmin_strength_runner.py`** — 對應的網路層：建立→讀回驗證→排程，搭配 `create_garmin_strength_workouts.py` 使用。**沒有 dedup**（COROS 那邊 `create_workouts.py` 有「同日同名舊排程自動移除」，這裡沒有）——對已經排過的週期重跑會排出重複項目，重跑前先去 Garmin 行事曆手動確認。
- **`create_garmin_strength_workouts.py`** — 建立並排上重複使用的肌力課表。**不是 markdown 驅動**——肌力routine通常不像跑步計畫那樣每週變動，所以動作/組數/次數直接寫在 `build_workouts()` 裡，**範本裡是佔位範例，換成你自己的內容**，用 `--week-start` 算出要排的日期：
  ```bash
  python create_garmin_strength_workouts.py --week-start 2026-09-21 --dry-run
  uv run create_garmin_strength_workouts.py --week-start 2026-09-21
  ```
- **`analyze_garmin_data.py`** — 同上，把 `data/garmin.db` 彙整成週/月摘要、長跑/強度課清單，輸出到 `data/garmin_analysis/`，同時鏡射回 `data/garmin.db`。
- **`verify_wiki_numbers.py`** — 把 `wiki/` 裡的彙整數字從 `data/analysis/*.json` 與 `data/coros.db` **重算一次再對 diff**。wiki 頁面用 `<!-- derived: <checker> src=... -->`（表格）或 `<!-- derived-scalars: ... -->`（內文數字）標記，腳本重算後比對，數值比較容忍格式差異。只用標準庫，任何 Python 3 都能跑；`--list` 列出已註冊的 checker。`githooks/post-commit` 在 commit 動到 `wiki/` 或 `data/` 時自動跑，**只報告不阻塞**。規則細節與新增 checker 的方式見 `wiki/WIKI.md`「衍生數字的對帳規則」，測試在 `tests/test_verify_wiki_numbers.py`（重點是重播真實錯誤的負面案例）。**這個範本裡已註冊的 checker（例如 `marathon_race_count`）是原專案的真實範例**，拿掉個人資料後對應來源檔不一定存在，請依你自己的資料結構調整或移除。
- **`data/`** — 抓取與分析結果。範本裡是空的，見 [data/README.md](data/README.md)；要不要進版控是你的選擇。

## 使用方式

兩支腳本都需要用 `coros-training-mcp` 工具自帶的 Python 環境執行（含 httpx/pydantic 依賴，並沿用已儲存的 COROS 登入 token）：

```bash
"C:\Users\<you>\AppData\Roaming\uv\tools\coros-training-mcp\Scripts\python.exe" fetch_training_data.py           # 預設近4個月，日常增量同步用
"C:\Users\<you>\AppData\Roaming\uv\tools\coros-training-mcp\Scripts\python.exe" fetch_training_data.py --full-history  # 帳號全部歷史，第一次跑或想補完整歷史時用
"C:\Users\<you>\AppData\Roaming\uv\tools\coros-training-mcp\Scripts\python.exe" analyze_training.py
```

```bash
python create_workouts.py --plan doc/example-training-plan.md --dry-run
python create_workouts.py --plan doc/example-training-plan.md
python3 -m unittest discover tests -v      # 純函式測試，不需要 venv
```

Garmin 課表同步用法對稱（`--dry-run` 不需要 `garminconnect`，正式執行用 `uv run` 自動安裝依賴）：

```bash
python create_garmin_workouts.py --plan doc/example-training-plan.md --dry-run
uv run create_garmin_workouts.py --plan doc/example-training-plan.md
```

⚠️ **測試 Garmin 重複組（`>>> 重複 N 組 ... <<<`）時，`unittest discover` 這樣裸跑會靜默少測**：
`garminconnect[workout]` 的 `[workout]` extra 不能省，它帶的是 **pydantic ≥2**。只裝
`garminconnect`（沒有 `[workout]`）會拿到 pydantic 1，而 `WorkoutSegment.workoutSteps`
宣告成 `list[ExecutableStep | RepeatGroup]`——**pydantic 1 沒有那種 union 支援，會把
RepeatGroup 靜默轉型成 ExecutableStep 並丟掉 `numberOfIterations`**，上傳一個沒有重複
次數的壞課表且完全不報錯。`garmin_runner._build_repeat_group()` 現在會在偵測到這種轉型
時直接丟 `RuntimeError`，但要先裝對依賴這個檢查才有機會跑到。完整跑測試（含這個情境的
回歸測試）用：

```bash
uv run --with pytest --with "garminconnect[workout]" --with fit-tool --with fitdecode \
    python -m pytest tests -q
```

直接用系統 `python` 執行 `fetch_training_data.py` 也可以——它偵測不到 `coros_api` 時會自動改用上述 venv 重新啟動。`analyze_training.py` 只用標準庫（含 `sqlite3`），任何 Python 3 都能跑。

其他參數：`--since YYYYMMDD` 指定固定起始日；`--refresh-recent-days N`（預設14）控制多近的活動一律重抓細節而不是讀快取（COROS App 事後補的訓練感受評分/備註需要重抓才看得到）。

### 儲存設計：依功能拆檔的 SQLite，而不是一個大 JSON

早期版本把所有活動細節塞進單一 JSON，累積幾個月就會膨脹到幾十 MB，且容易被誤植進 git 版控（`.gitignore` 沒排除，每次 fetch 都會讓 repo 變大）。改成依功能分層：

1. **`data/coros.db`（SQLite）** — 所有「會被拿來查詢/彙整」的欄位：活動摘要、訓練感受評分（`feelType`）、疲勞度/Training Effect/Performance 分數、每日生理指標、睡眠階段、課表庫、排定行事曆，加上 `analyze_training.py` 鏡射回來的週/月分析表。單一事實來源；`githooks/post-commit` 有偵測 `data/coros.db` 變更，會排進 `wiki/.pending-ingest`（見 `wiki/WIKI.md` 與上方「git hook 安裝」）。
2. **`data/detail/activities_YYYY.db`（依年份 + 大小拆檔）** — 每個年份一個 SQLite 檔，裝真正大的巢狀明細（分段、HR/配速 zone、裝置資訊、天氣、爬升軌跡），`activity_detail(activity_id, start_time, detail_json)` 單表。若整包塞一個檔會超過 **GitHub 單檔 100MB push 限制**，所以按年份拆；若哪一年份的單檔逼近 50MB，`fetch_training_data.py` 會自動另開 `_part2`/`_part3` 接續寫入，不需要重新分配既有檔案。`data/coros.db` 的 `activities.detail_db_path` 欄位指向對應 shard。
3. `data/analysis/*.json` — 供 wiki ingest / `marathon-coach` skill 使用的小型彙整輸出（`analyze_training.py` 的權威輸出仍是這層 JSON；鏡射進 `coros.db` 的分析表是同一份資料的第二種存取方式，不是另一個事實來源）。

**增量策略**：活動列表、每日生理指標、睡眠、排定行事曆都是重新整段抓（API 本身沒有「只給新資料」的介面），但**活動細節（`data/detail/*.db`）預設只在該 shard 已有這筆 `activity_id`、且活動發生在 `--refresh-recent-days` 天前時才跳過重抓**——歷史活動的細節基本不會變，這讓 `--full-history` 只需要付一次完整下載的成本，之後重跑都很快。

### fetch_training_data.py 輸出
- `data/coros.db` 資料表：`activities`（含 `feel_type`/`sport_note`/`tired_rate`/`aerobic_effect`/`anaerobic_effect`/`performance` 等訓練感受與效果分數）、`daily_metrics`（訓練負荷比、VO2max、RHR、睡眠HRV）、`sleep_records`（深睡/淺睡/REM/清醒分鐘數、睡眠心率、睡眠品質）、`workouts`（課表庫）、`scheduled_workouts`（排定行事曆，含未來已排的課表）、`fetch_log`（每次執行的抓取範圍與筆數）
- `data/detail/activities_YYYY[_partN].db` — 每筆活動的完整巢狀細節，依年份（必要時再依大小）拆檔
- `data/activities_summary.csv` — 精簡版活動列表（人工快速瀏覽用）
- `data/fetch_meta.json` — 最近一次抓取的日期範圍與各類筆數

### analyze_training.py 輸出（`data/analysis/` + 鏡射進 `data/coros.db`）
- `sessions.json` / `coros.db` 的 `sessions` 表 — 逐筆活動的整理資料（含跑步動態指標、訓練感受評分 `feel_type`、Performance 分數）
- `weekly_summary.json` / `monthly_summary.json`（表同名）— 週/月跑量、負荷、長跑與強度課次數
- `weekly_physiology.json`（表同名）— 週平均訓練負荷比、VO2max、RHR、睡眠HRV
- `long_runs.json` / `quality_sessions.json` — 標記出的長跑（>90分鐘）與強度課清單；`coros.db` 裡對應的是 `sessions` 表上的 `long_runs`/`quality_sessions` **view**（`WHERE is_long_run`/`WHERE is_quality`），不是另外複製一份資料

## Garmin 歷史資料補齊（fetch_garmin_data.py）

`fetch_garmin_data.py` 用途是補齊 COROS 之前的 Garmin Connect 歷史資料。抓取範圍預設到你切換裝置的前一天，避免跟 `data/coros.db` 重疊。這是**一次性/低頻執行的歷史補齊腳本**，跟 `fetch_training_data.py` 的每天/每週例行增量同步性質不同，日常不需要重跑。

**執行方式**：
```bash
uv run fetch_garmin_data.py
```
腳本開頭用 PEP 723 inline script metadata 宣告 `garminconnect` 相依，`uv run` 會自動裝好乾淨的 ephemeral 環境，不需要像 COROS 腳本那樣手動指定 venv 路徑。

**認證**：需要環境變數 `GARMIN_EMAIL` / `GARMIN_PASSWORD`。若帳號開了 MFA，第一次執行需要在互動式終端機輸入一次性驗證碼；之後 session token 會存在 `~/.garminconnect/`，之後重跑不需要再輸入。

**輸出**：
- `data/garmin.db`（SQLite）— `activities`、`daily_wellness`、`sleep_records`、`fetch_log`
- `data/garmin_detail/activities_YYYY.db` — 依年份拆檔的活動細節，含完整逐秒 GPS/感測器時序資料（`get_activity_details` 的原始回應，不是精簡摘要）
- `data/garmin_detail/splits.db` — 單一檔案（不拆年份），只放 `get_activity_splits` 的分段摘要，跟上面的巨量原始資料分開存，查 lap 摘要不用碰大檔案
- `data/garmin_activities_summary.csv` — 人工快速瀏覽用
- `data/garmin_fetch_meta.json` — 最近一次抓取的中繼資料

`data/garmin.db` 跟 `data/coros.db` 是**分開獨立的 DB**，不合併進同一張表：兩邊欄位語意不同（COROS 有 `feel_type`/`aerobic_effect` 等專屬欄位，Garmin 有 Body Battery/壓力等專屬欄位）。

**已知限制**：
- 舊款裝置大多沒有腕式睡眠追蹤，`sleep_records` 預期近乎是空的；`daily_wellness` 多數欄位（步數/靜止心率/Body Battery/壓力）同理常是 null，只有 `vo2max` 較常有值——這是裝置本身的限制，不是抓取邏輯的 bug。
- 目前也不下載原始 FIT/GPX 檔（跟 COROS 那邊的已知限制一致）。

### analyze_garmin_data.py 輸出（`data/garmin_analysis/` + 鏡射進 `data/garmin.db`）
結構跟 `analyze_training.py` 對 COROS 資料的處理一致，只是欄位範圍較窄（沒有 COROS 專屬的 `feel_type`/跑步動態欄位）：
- `sessions.json` / `garmin.db` 的 `sessions` 表 — 逐筆活動整理資料
- `weekly_summary.json` / `monthly_summary.json` — 週/月跑量、負荷、長跑與強度課次數
- `weekly_physiology.json` — 週平均 VO2max/RHR/步數
- `long_runs.json` / `quality_sessions.json` — 標記出的長跑（>90分鐘）與強度課清單；強度課偵測靠活動名稱關鍵字比對，舊款裝置的活動名稱大多是空的或「未分類」，偵測到的筆數會明顯偏少，不代表實際強度課次數這麼少

## 已知限制
- COROS 室內跑（跑步機）不回報觸地時間/垂直比等跑步動態欄位，分析時視為缺值處理，不會拉低週平均。
- 左右腳觸地平衡（L/R balance）部分裝置未回報，暫無資料。
- `coros-training-mcp` 的 `create_strength_workout` 工具的 `target_type` 參數只能傳整數（2=計時、3=次數），傳字串別名會被 COROS API 拒絕。
- 目前不下載每筆活動的原始 FIT/GPX 匯出檔（完整取樣率 GPS/感測器串流）——`coros_api.export_activity_file` 存在但尚未整合進 `fetch_training_data.py`，之後若需要逐秒配速/心率分析可以再加。
- 重訓動作目錄（`list_exercises`）、課表建構器目錄（`get_workout_builder_catalog`）是靜態參考資料，非個人訓練歷史，目前不下載。

## 訓練規劃現況
`marathon-coach` skill 示範了「每週滾動式調整」的規劃流程：長距離跑固定排某一天、強度課與長跑分開安排（避免同一次訓練疊加多個誘發因子），每週依實際回報的心率/配速/疼痛/天氣資訊調整下一週課表。實際的傷病/目標設定請填進你自己的 `my.md`（見 [my.md.example](my.md.example)）與 `.claude/skills/marathon-coach/SKILL.md` 的「Athlete profile」章節。
