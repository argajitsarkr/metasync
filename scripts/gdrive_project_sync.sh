#!/usr/bin/env bash
# =============================================================================
# gdrive_project_sync.sh
# Give ONE Google Drive project folder link (e.g. SNP260411_Results).
# The tool finds every subfolder inside it (1_Data, 2_Assembly, ... 10_Plots),
# downloads each one, verifies every file by MD5, and re-downloads any
# missing/corrupted file automatically.
#
# Usage:
#   ./gdrive_project_sync.sh -f "<DRIVE_FOLDER_LINK>" -d ~/metagenomics/SNP260411_Results
#
# Options:
#   -f LINK   Drive project folder link or ID                       (required)
#   -d DIR    Local destination folder                              (required)
#   -l        List subfolders (with sizes) and exit - nothing downloaded
#   -o NAME   Only these subfolder(s)  (repeatable, e.g. -o 3_MAG -o 10_Plots)
#   -e NAME   Skip these subfolder(s)  (repeatable, e.g. -e 1_Data)
#   -q        Quick verify (name + size only, no MD5)
#   -j N      Parallel downloads (default 8)
#   -r NAME   rclone remote name (default gdrive)
#   -s        Folder is in "Shared with me" (not needed for link-shared folders)
#   -h        Help
# =============================================================================
set -uo pipefail

LINK=""; DEST=""; LIST=0; QUICK=""; JOBS=8; REMOTE="gdrive"; SHARED=""
declare -a ONLY=() SKIP=()

usage(){ sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
while getopts "f:d:lo:e:qj:r:sh" opt; do
  case $opt in
    f) LINK="$OPTARG";; d) DEST="$OPTARG";; l) LIST=1;;
    o) ONLY+=("$OPTARG");; e) SKIP+=("$OPTARG");; q) QUICK="--size-only";;
    j) JOBS="$OPTARG";; r) REMOTE="$OPTARG";; s) SHARED="--drive-shared-with-me";;
    h|*) usage;;
  esac
done
[[ -z "$LINK" ]] && { echo "ERROR: -f is required"; usage; }
[[ $LIST -eq 0 && -z "$DEST" ]] && { echo "ERROR: -d is required"; usage; }

# ---- requirements ------------------------------------------------------------
if ! command -v rclone >/dev/null; then
  echo "Installing rclone..."; curl -fsSL https://rclone.org/install.sh | sudo bash
fi
if ! rclone listremotes | grep -qx "${REMOTE}:"; then
  echo "A browser will open - sign in to Google and click Allow."
  rclone config create "$REMOTE" drive scope=drive.readonly || exit 1
fi

if   [[ "$LINK" =~ folders/([A-Za-z0-9_-]+) ]]; then ID="${BASH_REMATCH[1]}"
elif [[ "$LINK" =~ id=([A-Za-z0-9_-]+) ]];      then ID="${BASH_REMATCH[1]}"
else ID="$LINK"; fi

RC=(--drive-root-folder-id "$ID" $SHARED)
count(){ if [[ -s "$1" ]]; then grep -c . "$1" || true; else echo 0; fi; }
in_list(){ local x="$1"; shift; for i in "$@"; do [[ "$i" == "$x" ]] && return 0; done; return 1; }

# ---- discover subfolders -------------------------------------------------------
echo "Reading folder list from Google Drive..."
mapfile -t ALL < <(rclone lsf "${REMOTE}:" "${RC[@]}" --dirs-only | sed 's#/$##' | sort -V)
[[ ${#ALL[@]} -eq 0 ]] && { echo "No subfolders found (wrong link? try -s)"; exit 1; }

if [[ $LIST -eq 1 ]]; then
  printf "%-35s %10s %12s\n" "SUBFOLDER" "FILES" "SIZE"
  for d in "${ALL[@]}"; do
    s=$(rclone size "${REMOTE}:$d" "${RC[@]}" --json 2>/dev/null)
    n=$(echo "$s" | grep -o '"count":[0-9]*' | cut -d: -f2)
    b=$(echo "$s" | grep -o '"bytes":[0-9]*' | cut -d: -f2)
    printf "%-35s %10s %12s\n" "$d" "${n:-?}" "$(numfmt --to=iec "${b:-0}")B"
  done
  exit 0
fi

# ---- decide which subfolders to process --------------------------------------
TODO=()
for d in "${ALL[@]}"; do
  [[ ${#ONLY[@]} -gt 0 ]] && ! in_list "$d" "${ONLY[@]}" && continue
  [[ ${#SKIP[@]} -gt 0 ]] &&   in_list "$d" "${SKIP[@]}" && continue
  TODO+=("$d")
done
[[ ${#TODO[@]} -eq 0 ]] && { echo "Nothing selected."; exit 1; }

mkdir -p "$DEST/_logs"
RUN="$DEST/_logs/run_$(date +%Y%m%d_%H%M%S)"; mkdir -p "$RUN"
LOG="$RUN/rclone.log"
echo "Destination : $(realpath "$DEST")"
echo "Subfolders  : ${TODO[*]}"
echo "Logs        : $RUN"

# ---- loose files directly inside the project folder (e.g. README, md5 lists) --
rclone copy "${REMOTE}:" "$DEST" "${RC[@]}" --max-depth 1 --retries 10 \
  --log-file "$LOG" 2>/dev/null

# ---- download + verify each subfolder -----------------------------------------
declare -A RESULT
for d in "${TODO[@]}"; do
  echo; echo "================ $d ================"
  mkdir -p "$DEST/$d"; R="$RUN/$d"; mkdir -p "$R"

  for attempt in 1 2 3; do
    echo "[download, pass $attempt]"
    rclone copy "${REMOTE}:$d" "$DEST/$d" "${RC[@]}" \
      --transfers "$JOBS" --checkers 16 --drive-chunk-size 128M \
      --drive-acknowledge-abuse --retries 10 --low-level-retries 20 \
      --tpslimit 10 --progress --stats 30s --log-file "$LOG"

    echo "[verify: $([[ -n $QUICK ]] && echo size || echo MD5)]"
    rm -f "$R"/*.txt
    rclone check "${REMOTE}:$d" "$DEST/$d" "${RC[@]}" $QUICK --one-way --checkers 16 \
      --match "$R/OK.txt" --missing-on-dst "$R/MISSING.txt" \
      --differ "$R/CORRUPTED.txt" --error "$R/ERRORS.txt" --log-file "$LOG" 2>/dev/null

    OK=$(count "$R/OK.txt"); MI=$(count "$R/MISSING.txt")
    CO=$(count "$R/CORRUPTED.txt"); ER=$(count "$R/ERRORS.txt")
    echo "  OK: $OK  missing: $MI  corrupted: $CO  errors: $ER"

    if (( MI + CO + ER == 0 )); then RESULT[$d]="OK ($OK files)"; break; fi
    # remove corrupted copies so the next pass downloads them fresh
    if (( CO > 0 )); then
      while IFS= read -r f; do rm -f "$DEST/$d/$f"; done < "$R/CORRUPTED.txt"
    fi
    RESULT[$d]="PROBLEM: $MI missing, $CO corrupted, $ER errors"
  done
done

# ---- final summary --------------------------------------------------------------
echo; echo "======================= SUMMARY ======================="
FAIL=0
for d in "${TODO[@]}"; do
  printf "%-30s %s\n" "$d" "${RESULT[$d]}"
  [[ "${RESULT[$d]}" == PROBLEM* ]] && FAIL=1
done
echo "======================================================="
( cd "$DEST" && find . -type f ! -path "./_logs/*" ! -name "*.partial" -print0 \
    | xargs -0 md5sum > "$RUN/MANIFEST.md5" ) 2>/dev/null
echo "Local checksum manifest: $RUN/MANIFEST.md5"
if [[ $FAIL -eq 0 ]]; then
  echo "ALL SELECTED FOLDERS DOWNLOADED AND VERIFIED."
else
  echo "Some folders still have problems - just run the same command again."
  exit 1
fi
