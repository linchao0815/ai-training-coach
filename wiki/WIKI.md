# WIKI.md — 訓練知識庫 Schema

本檔定義 `wiki/` 這個由 LLM 維護的知識庫的結構與工作流程。模式源自 karpathy 的 *LLM Wiki*（RAG 是查詢時重新檢索；本模式是**把知識一次編譯進持久化、互相連結的 markdown，之後只增量維護**）。

**範疇由你決定**：這份範本假設 wiki 是使用者的個人訓練/身體知識庫，但欄位與白名單都可以照你的用途調整（例如換成任何需要長期累積、互相參照的領域知識）。

**分工**：使用者只負責篩選來源、探索、問好問題；**wiki 由 LLM 完全維護**（摘要、交叉引用、歸檔、一致性）。使用者讀，LLM 寫。

---

## 三層架構

1. **原始來源 (raw sources)** — 不可變、事實來源。LLM 只讀不改。清單見下方「來源白名單」。
2. **wiki (`wiki/`)** — LLM 擁有的 markdown 頁面（概覽、實體、概念、來源摘要）。LLM 建立/更新/維護交叉引用。
3. **schema (本檔)** — 告訴 LLM wiki 怎麼組織、有哪些慣例、ingest/query/lint 怎麼跑。與使用者共同演進。

## 來源白名單（ingest 對象）

commit 時由 `githooks/post-commit`、`git pull` 時由 `githooks/post-merge` 依此清單偵測差異並排入佇列（兩支共用 `githooks/lib-queue.sh` 的比對實作；hook 本體在版控裡，靠 `core.hooksPath` 生效，安裝方式見 [README.md](../README.md)）。**下表是範例，請依你實際的資料來源調整**：

| 來源 | 說明 | ingest 方式 |
|---|---|---|
| `my.md` | 你的背景（傷病史、目標、跑量、裝備） | 全文精讀 |
| `README.md` | 專案工具集說明 | 全文精讀 |
| `doc/*.md` | 訓練計畫、賽事史、bug 診斷、參考資料 | 全文精讀 |
| `data/analysis/*.json` | 週/月摘要、長跑、強度課、生理指標 | 全文精讀（皆為小檔） |
| `data/coros.db`、`data/garmin.db` | SQLite：活動摘要、訓練感受、每日生理指標、睡眠、課表庫、排定行事曆 | **不逐字讀**：這是二進位檔，commit diff 只會顯示 binary changed。用 `sqlite3` 查詢彙整關鍵統計後 ingest，不整個 dump 進 wiki 頁 |
| `data/detail/`、`data/garmin_detail/` | 每筆活動的分段/裝置/天氣等大型巢狀明細 | **不逐字讀，不整批掃描**：只有在需要深入某幾筆活動細節時才查對應 shard |
| `raw/*.json` | 使用者手動放入的原始資料匯出檔（例如健檢報告），屬不可變事實來源 | 視資料類型精讀/結構化摘要；避免逐字貼個資與敏感明細，優先抽出日期範圍、類別、關鍵時間軸、異常/新增事實 |

**排除**：`wiki/` 自身（避免迴圈）、`.pending-ingest`、`__pycache__/`、腳本本身（`*.py`）、`.mcp.json`、`.claude/`、`CLAUDE.md`/`AGENTS.md`（那是「怎麼跟這個 repo 協作」的指引，不是領域知識）。

**維護 hook 時的已知坑（保留自實戰記錄，供參考）**：
1. **白名單要對稱**：新增一種資料來源時，多個平台版本（例如 COROS 與 Garmin）要一起加，否則會漏掉一半。
2. **非 ASCII 檔名必須用 `core.quotepath=false`**：git 預設把中文/非 ASCII 路徑輸出成帶引號的八進位轉義字串，會讓 shell glob pattern 比對失敗。
3. **rebase 期間必須跳過排隊**：rebase 會為每個重放的 commit 各觸發一次 post-commit，但那些內容早已 ingest 過，會灌進大量假資料。用 `.git/rebase-merge` / `.git/rebase-apply` 是否存在來判斷並跳過。`git cherry-pick` 刻意不跳過——那是把變更帶進這個分支，對本分支而言是新到達的內容。
4. **`.pending-ingest` 不進版控，所以 pull 進來的變更從來沒排過隊**：佇列是 gitignore 的本機檔案，A 機器 commit 時排的隊，B 機器 `git pull` 之後不會出現，而且這個洞是靜默的、容易被誤讀成「都處理完了」。修法是另外用 `githooks/post-merge` 比對 `ORIG_HEAD..HEAD`。**涵蓋不到的**：`git pull --rebase` 走 `post-rewrite`；`git reset --hard <remote>` 完全不觸發任何 hook。判斷 pull 進來的內容有沒有被 ingest，最可靠的是看 [log](log.md) 有沒有對應紀錄。
5. **hook 檔案必須有 executable bit，否則 git 直接不執行它**：新增進版控的 hook 檔如果模式是 `100644`，git 不會執行。用 `git update-index --chmod=+x githooks/<hook>` 修正，並用 `git ls-files -s githooks/` 確認模式是 `100755`。

## 目錄與頁型

```
wiki/
  WIKI.md          # 本 schema
  index.md         # 內容目錄（每次 ingest 更新）
  log.md           # 時間軸，append-only
  .pending-ingest  # post-commit / post-merge 寫入的待 ingest 佇列（git 忽略，不進版控）
  overview.md      # 綜合論點 / 全局圖像
  entities/        # 「東西」：人、傷病、裝備、比賽、健康數據/生理指標…
  concepts/        # 「主題」：負荷管理、週結構、單位慣例、訓練方法論…
  sources.md       # 每個原始來源的一句話摘要 + 連結
```

**頁面慣例**：
- 每頁一個主題，開頭用 YAML frontmatter：
  ```yaml
  ---
  title: 頁面標題
  type: entity | concept | overview | source-index
  updated: YYYY-MM-DD
  sources: [my.md, doc/xxx.md]   # 本頁綜合自哪些來源
  ---
  ```
  **`sources` 裡標記來源類別時，類別跟檔名之間不要用冒號**（例如寫 `gdrive 檔名`，不要寫 `gdrive:檔名`）——Obsidian 會把「冒號後接非空白字元」解析成 URI scheme，frontmatter 清單裡的每一項又會被渲染成可點擊連結，點下去就會跳出無效外部連結的錯誤視窗。
  **同理，`sources` 陣列裡也不要放任何連結語法**：這裡的 `sources:` 是用 YAML flow-style（`[a, b, c]`）寫的，`[[` 在 YAML 裡會被解析成「陣列裡包一個陣列」而不是字面上的雙方括號，會讓整個清單解析壞掉。`sources` 就維持純文字清單，精確的檔案層級連結改放在頁面正文。
- **一律用標準 markdown 相對連結**（`[顯示文字](../entities/xxx.md)`），**不要用 Obsidian 的 `[[wiki-link]]` 雙括號語法**：GitHub 在 repo 內的 `.md` 檔案不支援雙括號（只有 GitHub Wiki 那個功能支援），會原樣印成純文字；標準 markdown 相對連結則是 Obsidian 與 GitHub 兩邊都能點。連結要多（孤立頁是 lint 要抓的問題）。
- 有矛盾時**明確標記** `> ⚠️ 矛盾：…（來源 A vs 來源 B）`，不要默默選一邊。
- 陳述盡量附來源與日期，方便日後 lint 判斷是否過時。

## 工作流程

### Ingest（攝入）
觸發：使用者說「ingest」、或 `.pending-ingest` 非空。
0. **先跑 `python3 verify_wiki_numbers.py`**（見下方「衍生數字的對帳規則」）。有 drift 先修，不要在有 drift 的頁面上疊新內容。
1. 讀佇列（`wiki/.pending-ingest`，格式 `commit<TAB>status<TAB>path`）去重，取每個 path 最新狀態。
2. 逐一讀來源（大檔依上表用抽樣/腳本）。
3. 更新受影響的 wiki 頁：新事實併入既有 entity/concept 頁；沒有對應頁就新建；修訂 `overview.md` 綜合論點；更新 `sources.md`。一個來源常牽動數個頁面。
4. 更新 `index.md`。
5. 在 `log.md` append 一筆 ingest 紀錄。
6. **清空 `.pending-ingest`**（處理完的行移除）。
先跟使用者確認要點再落檔，除非他要求批次無人監督。

### Query（查詢）
1. 先讀 `index.md` 找相關頁 → 讀進去 → 綜合出附引用的答案。
2. 好的答案回填成新頁（比較表、分析、新發現的連結），別讓它只留在對話裡。
3. 需要動到「規劃/檢視下一週訓練」時，改用 `marathon-coach` skill（見下）。

### 衍生數字的對帳規則

**背景**：彙整出來的數字（比例、平均、跨表統計）比逐筆抄錄的數字更容易錯，而且**沒有東西會變紅的規則不是防線**——曾經發生過整欄配速是佔位值放了好幾週都沒被發現，源頭資料其實一直是乾淨的，錯的只有頁面自己彙整出來的數字。

分界很清楚：**抄過來的沒事，算出來的沒人對帳**。所以規則只針對後者。

#### A. 彙整數字一律加錨點，由 `verify_wiki_numbers.py` 重算對帳

這是 `create_workouts.py` 的 `verify()` 在課表那邊做的事（建完讀回來，對不上就拒絕排程），搬到 wiki。表格上方放：

```markdown
<!-- derived: some_table_name src=data/analysis/some_file.json -->
```

內文數字用 `derived-scalars`。腳本從原始檔重算後 diff，數值比較容忍格式差異（`32.5`／`32.5 km`／`**32.5 km**` 視為相同）。

- 新增一張衍生表 → **同時**在 `verify_wiki_numbers.py` 註冊 checker。沒 checker 的錨點會被報成錯，沒錨點的 checker 也會（避免錨點被順手刪掉後靜默失效）。
- `githooks/post-commit` 在 commit 動到 `wiki/` 或 `data/` 時自動跑，**只報告不阻塞**，跟 ingest 佇列同一個設計原則。
- 測試在 `tests/test_verify_wiki_numbers.py`，**重點是負面案例**——重播真實錯誤，確認會變紅。只會綠的檢查不是防線。

> `verify_wiki_numbers.py` 裡目前註冊的 checker 是原專案的真實範例（例如 `marathon_race_count`），拿掉個人資料後這些 checker 對應的來源檔不一定存在——請依你自己的資料結構調整或移除，範例的價值在於示範「怎麼寫一個 checker」。

#### B. 每個衍生數字都要說得出分母

寫任何比例/平均前，先在文字裡講清楚母體是「全部活動」還是某個子集。同一段裡切換母體必須明講並附對照。

#### C. 不要寫會過期的絕對陳述

「從未」「全部都是」「一次都沒有」是把當下快照寫成永久事實。改寫成帶日期的相對陳述（「截至 YYYY-MM-DD 為 3/83」）——不需要額外機制，而且讀的人自己看得出來新不新。

### Lint（健檢）
使用者說「lint wiki」時，檢查並回報：頁面間矛盾、被新來源取代的過時說法、孤立頁（無連入）、被提到卻沒有專頁的重要概念、缺失的交叉引用、可用網路查補的資料缺口。只回報+建議，不擅自大改。

## 與現有機制的關係
- **`marathon-coach` skill**：負責「規劃/檢視單一訓練週」，是 wiki 眾多使用場景之一。wiki 是它的知識底稿（背景、傷況、負荷史、單位陷阱）；skill 產出的新週計畫（`doc/YYYY-Wxx-*.md`）會在 commit 後被 hook 偵測、回流 ingest 進 wiki。
- **記憶系統**（`memory/`, `MEMORY.md`）：跨對話的個人化偏好/回饋/專案狀態。wiki 是領域知識本身。兩者互補：記憶記「怎麼跟這位使用者合作」，wiki 記「這個人身體/健康相關的事實」。

## Log 格式
每筆一行標題，固定前綴以便 `grep '^## \[' log.md | head`：
```
## [YYYY-MM-DD] ingest | <來源清單摘要>
## [YYYY-MM-DD] query  | <問題摘要>
## [YYYY-MM-DD] lint   | <發現摘要>
```

**新增規則**：`log.md` 是「最新一筆在檔案最上面」（append-only 指內容不刪減，不是指物理上永遠加在檔尾）。新增一筆時：
- **插入位置是檔案第一個 `## [` 之前**（緊接在頂部說明段落之後），不是檔尾。
- **絕對不要**用 Edit 工具找檔案中間某段舊文字當 anchor、在那段文字後面接新內容——這樣做等於把新 entry 插進舊 entry 內部，會把舊 entry 的段落切斷、還會讓檔案的時間順序整個錯亂。正確做法：只針對「檔案最開頭的第一個 `## [` 標題」做字串比對插入，或整份讀出用程式重組後寫回，不要對中段的舊內容做字串定位。
