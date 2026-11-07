#!/usr/bin/env bash
# =============================================================================
# gdrive_meta_downloader.sh
# Download metagenomics data (HQ data, raw data, ...) from Google Drive folders
# into ONE local project directory, preserving each folder's sub-structure.
# Backend: rclone (resumable, checksum-verified, handles >100 GB and 1000s of files)
#
# Usage:
#   ./gdrive_meta_downloader.sh -d /data/metagenomics \
#       -f "HQ_data=<DRIVE_FOLDER_ID_OR_LINK>" \
#       -f "raw_data=<DRIVE_FOLDER_ID_OR_LINK>"
#
# Options:
#   -d DIR     Destination root (default: ./metagenomics_data)
#   -f N=ID    Local subfolder name = Drive folder ID or share link (repeatable)
#   -r NAME    rclone remote name (default: gdrive)
#   -j N       Parallel transfers (default: 8)
#   -s         Folder is "Shared with me" (not in your own Drive)
#   -n         Dry run (list what would be downloaded)
#   -h         Help
# =============================================================================
set -euo pipefail

DEST="./metagenomics_data"; REMOTE="gdrive"; JOBS=8; SHARED=""; DRY=""
declare -a FOLDERS=()

usage(){ sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while getopts "d:f:r:j:snh" opt; do
  case $opt in
    d) DEST="$OPTARG";; f) FOLDERS+=("$OPTARG");; r) REMOTE="$OPTARG";;
    j) JOBS="$OPTARG";; s) SHARED="--drive-shared-with-me";;
    n) DRY="--dry-run";; h|*) usage;;
  esac
done
[[ ${#FOLDERS[@]} -eq 0 ]] && { echo "ERROR: give at least one -f NAME=ID"; usage; }

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOGFILE"; }

# ---- 1. Ensure rclone is installed ------------------------------------------
if ! command -v rclone >/dev/null 2>&1; then
  echo "rclone not found - installing (needs sudo)..."
  curl -fsSL https://rclone.org/install.sh | sudo bash
fi

# ---- 2. Ensure the Google Drive remote exists --------------------------------
if ! rclone listremotes | grep -qx "${REMOTE}:"; then
  echo "No rclone remote '${REMOTE}' found. A browser window will open -"
  echo "sign in to the Google account that owns/has access to the data and click Allow."
  rclone config create "$REMOTE" drive scope=drive.readonly \
    || rclone config
fi

mkdir -p "$DEST/_logs"
LOGFILE="$DEST/_logs/download_$(date +%Y%m%d_%H%M%S).log"
log "Destination: $(realpath "$DEST")"

# ---- 3. Extract folder ID from a link or plain ID ---------------------------
get_id(){
  local s="$1"
  if [[ "$s" =~ folders/([A-Za-z0-9_-]+) ]]; then echo "${BASH_REMATCH[1]}"
  elif [[ "$s" =~ id=([A-Za-z0-9_-]+) ]]; then echo "${BASH_REMATCH[1]}"
  else echo "$s"; fi
}

# ---- 4. Download each folder -------------------------------------------------
FAILED=0
for pair in "${FOLDERS[@]}"; do
  NAME="${pair%%=*}"; ID="$(get_id "${pair#*=}")"
  TARGET="$DEST/$NAME"; mkdir -p "$TARGET"
  log "=== $NAME  (Drive ID: $ID) -> $TARGET ==="

  if rclone copy "${REMOTE}:" "$TARGET" \
        --drive-root-folder-id "$ID" $SHARED $DRY \
        --transfers "$JOBS" --checkers 16 \
        --drive-chunk-size 128M --drive-acknowledge-abuse \
        --retries 10 --low-level-retries 20 \
        --tpslimit 10 --progress --stats 30s \
        --log-file "$LOGFILE" --log-level INFO; then
    log "Copy finished: $NAME"
    if [[ -z "$DRY" ]]; then
      log "Verifying (MD5 checksum) $NAME ..."
      if rclone check "${REMOTE}:" "$TARGET" --drive-root-folder-id "$ID" \
           $SHARED --one-way --checksum --log-file "$LOGFILE"; then
        log "VERIFIED OK: $NAME"
      else
        log "WARNING: mismatches in $NAME - re-run script to resume/fix"; FAILED=1
      fi
    fi
  else
    log "ERROR: download failed for $NAME - re-run to resume"; FAILED=1
  fi
done

# ---- 5. Summary + local manifest ---------------------------------------------
if [[ -z "$DRY" ]]; then
  log "Writing manifest (file list + sizes + md5)..."
  ( cd "$DEST" && find . -type f ! -path "./_logs/*" -print0 \
      | xargs -0 md5sum > "_logs/MANIFEST.md5" )
  log "Summary:"
  for pair in "${FOLDERS[@]}"; do
    N="${pair%%=*}"
    log "  $N : $(find "$DEST/$N" -type f | wc -l) files, $(du -sh "$DEST/$N" | cut -f1)"
  done
fi
[[ $FAILED -eq 0 ]] && log "ALL DONE." || { log "Finished WITH ERRORS - see $LOGFILE"; exit 1; }
