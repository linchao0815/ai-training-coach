#!/bin/sh
#
# lib-queue.sh — post-commit 與 post-merge 共用的白名單比對與數字對帳
#
# 為什麼抽出來：白名單原本只寫在 post-commit 裡。2026-07-18 曾發生「只加了
# data/coros.db 沒對稱加 data/garmin.db」的漏網（見 wiki/log.md），而一份清單
# 抄成兩份之後，那種不對稱會變成常態而不是意外。所以兩個 hook 共用這一份。
#
# 白名單定義的權威來源仍是 wiki/WIKI.md 的「來源白名單」表；下面的 case pattern
# 必須跟那張表保持同步。
#
# 本檔不是 hook，不會被 git 直接呼叫，由同目錄的 post-commit / post-merge 用
# `.` 載入。

# queue_changes <sha> <changes>
#
# <changes> 是 `git ... --name-status` 的輸出（status<TAB>path 每行一筆）。
# 比對白名單後把 `sha<TAB>status<TAB>path` 寫到 stdout，一行一筆；沒有任何一筆
# 命中就不輸出任何東西。
#
# 只 append、不去重；去重由讀佇列的一方做（WIKI.md ingest 流程第 1 步）。
queue_changes() {
	_sha=$1
	_changes=$2

	printf '%s\n' "$_changes" | while IFS='	' read -r status path rest; do
		[ -n "${path:-}" ] || continue

		# 就算有 quotepath=false，含空白/特殊字元的路徑 git 仍會加上雙引號；
		# 去掉頭尾引號讓下面的 pattern 比對得到（佇列裡也存去引號後的乾淨路徑）。
		case "$path" in
			'"'*'"') path=$(printf '%s' "$path" | sed 's/^"//; s/"$//') ;;
		esac

		case "$path" in
			# ---- 排除（優先於白名單）----
			wiki/*)                         continue ;;  # wiki 自身，避免 ingest 迴圈
			*/__pycache__/*|__pycache__/*)  continue ;;

			# ---- 白名單：文件 ----
			# 注意：CLAUDE.md/AGENTS.md 刻意不在白名單裡——那是「怎麼跟這個 repo 協作」
			# 的指引，不是使用者身體/健康的領域知識，不該觸發 ingest。
			my.md|README.md) ;;
			doc/*.md)        ;;

			# ---- 白名單：analyze_*.py 的小型彙整輸出 ----
			data/analysis/*.json|data/garmin_analysis/*.json) ;;

			# ---- 白名單：fetch_*.py 的中層/中繼資料 ----
			data/activities_summary.csv|data/fetch_meta.json)               ;;
			data/garmin_activities_summary.csv|data/garmin_fetch_meta.json) ;;

			# ---- 白名單：SQLite 事實來源（binary，ingest 時用 sqlite3 查而非逐字讀）----
			data/coros.db|data/garmin.db)              ;;
			data/detail/*.db|data/garmin_detail/*.db)  ;;

			# ---- 白名單：使用者手動放入的原始健康資料匯出 ----
			raw/*.json|raw/*.JSON) ;;

			# ---- 白名單：使用者手動放入的新聞/文章全文擷取（.md） ----
			raw/news_*.md) ;;

			# ---- 其他一律不排隊（腳本本身、.mcp.json、skill 等）----
			*) continue ;;
		esac

		printf '%s\t%s\t%s\n' "$_sha" "$status" "$path"
	done
}

# write_queue <root> <label> <dry_run> <matched-lines>
#
# 把 queue_changes 的輸出寫進 wiki/.pending-ingest 並印一行摘要。
# <dry_run> 是 "--dry-run" 時只印出內容、不寫檔。
write_queue() {
	_root=$1
	_label=$2
	_dry=$3
	_lines=$4

	[ -n "$_lines" ] || return 0

	if [ "$_dry" = "--dry-run" ]; then
		printf '=== dry-run，以下內容不會寫進佇列 ===\n'
		printf '%s\n' "$_lines"
		return 0
	fi

	_queue="$_root/wiki/.pending-ingest"
	printf '%s\n' "$_lines" >> "$_queue" 2>/dev/null || return 0
	_n=$(printf '%s\n' "$_lines" | wc -l | tr -d ' ')
	_total=$(wc -l < "$_queue" 2>/dev/null | tr -d ' ')
	printf 'wiki: %s有 %s 個來源檔進入 ingest 佇列（wiki/.pending-ingest 共 %s 筆待處理）\n' \
		"$_label" "$_n" "$_total"
}

# run_verifier <root> <changes>
#
# wiki 數字對帳（2026-08-16 新增）。**只報告、不阻塞**——post-commit/post-merge 的
# exit code git 本來就不看，這裡也不做任何會改動 working tree 的事。
# 只有在變更動到 wiki/ 或 data/ 時才跑（純程式碼的變更不需要）。
run_verifier() {
	_root=$1
	_changes=$2

	case "$_changes" in
		*wiki/*|*data/*) ;;
		*) return 0 ;;
	esac

	_verifier="$_root/verify_wiki_numbers.py"
	[ -f "$_verifier" ] || return 0
	command -v python3 >/dev/null 2>&1 || return 0

	# 只用標準函式庫，所以系統 python3 就夠（不像 fetch_training_data.py 需要
	# coros-training-mcp 那個 venv）。
	if ! _drift=$(cd "$_root" && python3 "$_verifier" --quiet 2>&1); then
		printf '%s\n' "$_drift"
		printf 'wiki: 數字對帳未通過（不阻塞）。修好後重跑 python3 verify_wiki_numbers.py 確認。\n'
	fi
}
