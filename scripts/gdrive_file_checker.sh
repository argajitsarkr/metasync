#!/usr/bin/env bash
# =============================================================================
# gdrive_file_checker.sh
# Check that every file in a Google Drive folder was downloaded correctly
# to a local folder (present, same size, same MD5 checksum).
#
# Usage:
#   ./gdrive_file_checker.sh -l ~/metagenomics/HQ_data -f "<DRIVE_FOLDER_LINK_OR_ID>"
#
# Options:
#   -l DIR    Local folder that holds the downloaded files   (required)
#   -f LINK   Google Drive folder link or ID                 (required)
#   -r NAME   rclone remote name (default: gdrive)
#   -s        Folder is "Shared with me"
#   -q        Quick check (name + size only, no MD5) - much faster
#   -x        Fix: re-download missing / corrupted files after checking
#   -h        Help
# =============================================================================
set -uo pipefail

LOCAL=""; LINK=""; REMOTE="gdrive"; SHARED=""; QUICK=""; FIX=0

usage(){ sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
while getopts "l:f:r:sqxh" opt; do
  case $opt in
    l) LOCAL="$OPTARG";; f) LINK="$OPTARG";; r) REMOTE="$OPTARG";;
    s) SHARED="--drive-shared-with-me";; q) QUICK="--size-only";;
    x) FIX=1;; h|*) usage;;
  esac
done
[[ -z "$LOCAL" || -z "$LINK" ]] && { echo "ERROR: -l and -f are required"; usage; }
[[ -d "$LOCAL" ]] || { echo "ERROR: local folder not found: $LOCAL"; exit 1; }
command -v rclone >/dev/null || { echo "ERROR: rclone not installed (run the downloader first)"; exit 1; }

# Extract folder ID from a share link
if   [[ "$LINK" =~ folders/([A-Za-z0-9_-]+) ]]; then ID="${BASH_REMATCH[1]}"
elif [[ "$LINK" =~ id=([A-Za-z0-9_-]+) ]];      then ID="${BASH_REMATCH[1]}"
else ID="$LINK"; fi

REPORT_DIR="$LOCAL/../_check_reports/$(basename "$LOCAL")_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$REPORT_DIR"
MODE=$([[ -n "$QUICK" ]] && echo "name + size" || echo "name + size + MD5")

echo "Checking : $(realpath "$LOCAL")"
echo "Against  : Google Drive folder $ID"
echo "Mode     : $MODE"
echo "Please wait..."

rclone check "${REMOTE}:" "$LOCAL" --drive-root-folder-id "$ID" $SHARED $QUICK \
  --checkers 16 --one-way \
  --combined   "$REPORT_DIR/all_files.txt" \
  --match      "$REPORT_DIR/OK.txt" \
  --missing-on-dst "$REPORT_DIR/MISSING.txt" \
  --differ     "$REPORT_DIR/CORRUPTED.txt" \
  --error      "$REPORT_DIR/ERRORS.txt" \
  --log-file   "$REPORT_DIR/rclone.log" 2>/dev/null

# Extra local files that are not on Drive (informational)
rclone check "${REMOTE}:" "$LOCAL" --drive-root-folder-id "$ID" $SHARED --size-only \
  --missing-on-src "$REPORT_DIR/EXTRA_LOCAL.txt" --log-file /dev/null 2>/dev/null

count(){ if [[ -s "$1" ]]; then grep -c . "$1" || true; else echo 0; fi; }
TOTAL=$(count "$REPORT_DIR/all_files.txt"); OK=$(count "$REPORT_DIR/OK.txt")
MISS=$(count "$REPORT_DIR/MISSING.txt");    BAD=$(count "$REPORT_DIR/CORRUPTED.txt")
ERR=$(count "$REPORT_DIR/ERRORS.txt");      EXTRA=$(count "$REPORT_DIR/EXTRA_LOCAL.txt")

echo
echo "================ RESULT ================"
printf "Files on Drive        : %s\n" "$TOTAL"
printf "Downloaded correctly  : %s\n" "$OK"
printf "MISSING locally       : %s\n" "$MISS"
printf "CORRUPTED / different : %s\n" "$BAD"
printf "Could not check       : %s\n" "$ERR"
printf "Extra local files     : %s (not on Drive, ignored)\n" "$EXTRA"
echo "========================================"

show(){ [[ $(count "$1") -gt 0 ]] && { echo "$2 (first 20):"; head -20 "$1" | sed 's/^/   /'; }; }
show "$REPORT_DIR/MISSING.txt"   "Missing files"
show "$REPORT_DIR/CORRUPTED.txt" "Corrupted files"
show "$REPORT_DIR/ERRORS.txt"    "Errors"
echo "Full lists saved in: $(realpath "$REPORT_DIR")"

PROBLEMS=$((MISS + BAD + ERR))
if [[ $PROBLEMS -eq 0 ]]; then
  echo "ALL FILES DOWNLOADED CORRECTLY."; exit 0
fi

if [[ $FIX -eq 1 ]]; then
  echo; echo "Fixing: re-downloading $((MISS + BAD)) file(s)..."
  cat "$REPORT_DIR/MISSING.txt" "$REPORT_DIR/CORRUPTED.txt" 2>/dev/null > "$REPORT_DIR/to_fix.txt"
  rclone copy "${REMOTE}:" "$LOCAL" --drive-root-folder-id "$ID" $SHARED \
    --files-from "$REPORT_DIR/to_fix.txt" --ignore-times \
    --transfers 8 --retries 10 --drive-acknowledge-abuse --progress
  echo "Done. Run the checker again to confirm."
else
  echo "Problems found. Re-run with -x to re-download only the bad/missing files."
fi
exit 1
