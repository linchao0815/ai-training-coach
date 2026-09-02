# data/

這個資料夾在範本裡是空的。第一次執行 `fetch_training_data.py`（或
`fetch_garmin_data.py`）之後，這裡會產生：

- `coros.db` / `garmin.db` — SQLite，活動摘要、生理指標、睡眠、課表庫、排定行事曆
- `detail/`、`garmin_detail/` — 每筆活動的巨大巢狀明細，依年份/大小拆檔
- `analysis/`、`garmin_analysis/` — `analyze_training.py` / `analyze_garmin_data.py`
  彙整出的週/月摘要、長跑清單、強度課清單（`wiki/` ingest 流程依賴這些小型 JSON 檔）
- `activities_summary.csv`、`fetch_meta.json` 等中繼資料

原專案把這些資料**全部進版控**，讓 clone 這個 repo 就拿到完整歷史。這是你的選擇——
如果你的資料是私人的，建議把 `data/` 加進 `.gitignore`；如果要跟原專案一樣公開，
記得先確認資料本身沒有你不想公開的內容。
