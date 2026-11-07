# MetaSync

**Download, verify and back up large sequencing datasets from Google Drive on Ubuntu — with a simple GUI.**

Sequencing providers often deliver metagenomics/genomics results as shared Google Drive folders. Downloading them through a browser is unreliable: Drive splits folders into dozens of ~2 GB zip parts, downloads silently fail, files get duplicated, and there is no easy way to prove that what is on your server is identical to what was delivered.

MetaSync solves this end-to-end:

| Step | What it does |
|---|---|
| **Download** | Pulls whole Drive folders (or selected subfolders) directly to your server — no zips, resumable, 8 files in parallel |
| **Verify** | Compares every local file with Drive by **name, size and MD5 checksum** — reports missing, corrupted and extra files |
| **Fix** | Re-downloads only the missing/corrupted files, automatically, up to 3 passes |
| **Back up** | Copies a verified folder to a second server over SSH (rsync) and verifies the copy by checksum |

Built on [rclone](https://rclone.org) and [rsync](https://rsync.samba.org).

---

## Contents

```
metasync/
├── metasync_gui.py              # the GUI application (all features)
├── install.sh                   # one-step installer for Ubuntu
└── scripts/                     # command-line versions (for SSH / automation)
    ├── gdrive_project_sync.sh   #   download + verify + fix every subfolder of a project
    ├── gdrive_meta_downloader.sh#   download specific folders
    └── gdrive_file_checker.sh   #   verify a local folder against a Drive folder
```

## Requirements

- Ubuntu 20.04 / 22.04 / 24.04 (or Debian-based Linux) with a desktop for the GUI
- Python 3 with Tkinter (`python3-tk`)
- rclone, rsync (installed automatically by `install.sh`)
- A Google account with access to the Drive folder (link-shared folders work)

## Installation

```bash
git clone https://github.com/argajitsarkr/metasync.git
cd metasync
./install.sh
```

Then open **MetaSync** from the Ubuntu app menu, or run:

```bash
/usr/bin/python3 metasync_gui.py
```

> **Conda users:** launch with `/usr/bin/python3` (system Python). Conda's Python often lacks a working Tk display backend.

---

## Using the GUI

### 1. Setup
Shows whether rclone is installed and Google Drive is connected.
- **Install rclone** — if missing (asks for your password).
- **Connect Google account** — opens a browser; sign in and click **Allow**. Done once; the token is stored in `~/.config/rclone/rclone.conf`.

### 2. Download project
1. Paste the Drive **folder link** (e.g. `https://drive.google.com/drive/folders/1AbC...`).
2. Choose **Save to** (check you have enough free disk space — shown in the log).
3. Click **Load subfolders** → select the ones you need (Ctrl/Shift-click, or **Select all**).
4. Click **Start download + verify**.

Each subfolder is downloaded, MD5-verified, and auto-fixed. A summary appears at the end:

```
================ SUMMARY ================
2_Assembly                          OK (34 files)
3_MAG                               OK (412 files)
ALL DOWNLOADED AND VERIFIED.
```

If the folder contains files directly (no subfolders), tick **Folder has no subfolders**.

### 3. Verify folder
Already have data on disk (e.g. from a browser download)? Check it:
1. **Local folder** → the folder on your server.
2. **Drive link** → the *matching* Drive folder (raw ↔ raw, HQ ↔ HQ).
3. **Check only** — report problems, or **Check + fix** — also re-download bad files.

Reports (`OK.txt`, `MISSING.txt`, `CORRUPTED.txt`, `EXTRA.txt`) are saved in `_check_reports/` next to the folder.

### 4. Backup to server
Copy a verified folder to another machine on your network.

One-time SSH key setup (in a terminal):
```bash
ssh-keygen -t ed25519          # press Enter at every prompt
ssh-copy-id user@192.168.1.10  # the backup server
```
Then in the GUI: fill **local folder**, **user@IP**, **folder on server** →
**Test connection + space** → **Start backup** → **Verify backup (MD5)**.

---

## Command-line usage (no GUI / over SSH)

Run long jobs inside `tmux` so they survive a closed terminal:
```bash
tmux new -s sync        # detach: Ctrl+B then D   |   reattach: tmux attach -t sync
```

**List a project's subfolders and sizes**
```bash
./scripts/gdrive_project_sync.sh -l -f "<DRIVE_FOLDER_LINK>"
```

**Download + verify everything (skip one folder)**
```bash
./scripts/gdrive_project_sync.sh -f "<DRIVE_FOLDER_LINK>" -d ~/data/Project -e 1_Data
```
Options: `-o NAME` only these subfolders · `-e NAME` skip · `-q` size-only verify · `-j N` parallel downloads

**Verify an existing folder (and fix with `-x`)**
```bash
./scripts/gdrive_file_checker.sh -l ~/data/raw -f "<DRIVE_FOLDER_LINK>" -x
```

**Back up to another server and verify**
```bash
rsync -avhP ~/data/raw/ user@IP:~/backup/raw/
rsync -avhn --checksum ~/data/raw/ user@IP:~/backup/raw/   # no file names listed = identical
```

---

## How it works

```
authenticate (OAuth) → list Drive folder (Google stores each file's MD5)
→ hash local files → compare → transfer only the difference → verify again
```

- **Folder IDs, not paths.** Every Drive object has an ID (`.../folders/<ID>`); `--drive-root-folder-id` lets link-shared folders work without adding them to your Drive.
- **Free remote checksums.** Google computes an MD5 for every uploaded file, so the remote side is verified without downloading anything. Only local files must be read — verification time scales with local data size.
- **Safe downloads.** rclone writes to a temporary file and renames it only when complete, so a half-downloaded file never carries the real name. Re-running skips finished files (resume).
- **Parallelism.** Drive throttles each file; downloading 8 at once fills your bandwidth. `--tpslimit` keeps API calls under Google's rate limit.
- **Size vs MD5.** Size-only checks catch truncated downloads; MD5 also catches same-size corruption. Use quick mode for a first pass, MD5 before analysis.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `0 files on Drive` / folder not found | Wrong link, or folder is in *Shared with me* — add `-s` (CLI) |
| Every file reported MISSING | Local folder paired with the wrong Drive folder |
| `403` / `rateLimitExceeded` | Wait an hour, or [create your own Google client ID](https://rclone.org/drive/#making-your-own-client-id) and re-run *Connect Google account* |
| Notice about *shared client_id being retired* | Harmless for now; create your own client ID (link above) to avoid future interruptions |
| `Could not log in without password` (backup) | Run `ssh-copy-id user@IP` once |
| GUI does not open from conda env | Use `/usr/bin/python3 metasync_gui.py` |
| Download stopped | Press **Start** again (or re-run the command) — it resumes |

## Data & privacy

MetaSync uses **read-only** Drive access (`drive.readonly`). Your Google token stays in `~/.config/rclone/rclone.conf` on your machine — never commit it. No data is sent anywhere except between Google Drive, your server and your backup server.

## Citation

If MetaSync helps your work, please cite this repository:

```
Sarkar, A. (2026). MetaSync: verified Google Drive download and backup for sequencing data.
GitHub repository, https://github.com/<your-username>/metasync
```

## License

MIT — see [LICENSE](LICENSE).
