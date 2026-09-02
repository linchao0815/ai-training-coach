# ai-training-coach — 專案指引

## LLM Wiki（`wiki/`）

本專案有一個由 LLM 維護的知識庫在 `wiki/`（模式與規則見 [wiki/WIKI.md](wiki/WIKI.md)）。它持續整合 `my.md`、`README.md`、`doc/*.md`、`data/analysis/*.json` 等來源的知識，避免每次對話都重新從原始檔拼湊背景。**範疇不限於跑步訓練**——這是設計給使用者全面的個人身體/健康知識庫，訓練/傷病只是範例份量最大的主題，抽血/健檢/基因檢測等其他健康資料同樣可以是獨立主線。

**每次對話開始時**，若使用者的請求與訓練規劃/傷病/健康資料/資料分析相關，先檢查 `wiki/.pending-ingest` 是否存在且非空：
- 存在且非空 → 用一句話告知使用者有幾筆待 ingest 的變更（來自哪些 commit/檔案），問是否現在處理。使用者同意後依 `wiki/WIKI.md` 的 Ingest 流程執行，完成後清空該檔。
- 不存在或空 → 不用特別提，直接使用 `wiki/index.md` 作為背景知識的起點（比重新讀所有原始檔快）。

一個 git commit 若動到白名單內的來源檔（見 `wiki/WIKI.md`），`githooks/post-commit` 會自動把它記進 `wiki/.pending-ingest`，不會阻塞 commit、也不會自動花 token 去 ingest —— 目的是讓「查看/處理」這個決定留在下一次真正的對話裡。

⚠️ 這個 hook 靠 `core.hooksPath` 生效，而 **git hook 不隨 clone 複製**。若在新環境 clone 這個 repo，必須先跑一次 `git config core.hooksPath githooks`（見 [README.md](README.md)），否則佇列會**靜默失效**（commit 照常成功、只是不再排隊，沒有任何錯誤訊息）。懷疑時用 `git config --get core.hooksPath` 確認。

## 其他
專案工具本身的使用方式見 [README.md](README.md)；跑者背景見 `my.md`（複製 [my.md.example](my.md.example) 並填入你自己的資料，`.gitignore` 已排除 `my.md`）。
