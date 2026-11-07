#!/usr/bin/env python3
"""
MetaSync - Google Drive metagenomics data manager (Ubuntu GUI)
  Tab 1  Setup      : install rclone, connect Google account
  Tab 2  Download   : pick a Drive project folder, tick subfolders, download + MD5 verify + auto-fix
  Tab 3  Verify     : compare any local folder with a Drive folder, optionally fix
  Tab 4  Backup     : copy a local folder to another server (rsync over SSH) and verify it

Run:      python3 metasync_gui.py
Shortcut: python3 metasync_gui.py --install   (adds "MetaSync" to the Ubuntu app menu)
Needs:    python3-tk  (sudo apt install python3-tk), rclone, rsync
"""
import os, re, sys, queue, shutil, threading, subprocess, tempfile, datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

REMOTE = "gdrive"
PCT = re.compile(r"(\d{1,3})%")


def folder_id(link: str) -> str:
    link = link.strip()
    for pat in (r"folders/([A-Za-z0-9_-]+)", r"id=([A-Za-z0-9_-]+)"):
        m = re.search(pat, link)
        if m:
            return m.group(1)
    return link


def count_lines(path):
    try:
        with open(path) as f:
            return sum(1 for l in f if l.strip())
    except FileNotFoundError:
        return 0


def read_lines(path):
    try:
        with open(path) as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return []


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MetaSync - Drive data manager")
        self.geometry("1000x760")
        self.q = queue.Queue()
        self.proc = None
        self.busy = False
        self.stop_flag = False
        self.logfile = None

        nb = ttk.Notebook(self)
        nb.pack(fill="x", padx=8, pady=6)
        self.tab_setup(nb)
        self.tab_download(nb)
        self.tab_verify(nb)
        self.tab_backup(nb)

        # shared bottom area: progress + log
        bottom = ttk.Frame(self)
        bottom.pack(fill="both", expand=True, padx=8, pady=4)
        top = ttk.Frame(bottom)
        top.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.status, font=("", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Stop", command=self.stop).pack(side="right")
        ttk.Button(top, text="Clear log", command=lambda: self.log.delete("1.0", "end")).pack(side="right", padx=4)
        self.pbar = ttk.Progressbar(bottom, maximum=100)
        self.pbar.pack(fill="x", pady=4)
        self.log = tk.Text(bottom, height=20, wrap="none", font=("Monospace", 9))
        sb = ttk.Scrollbar(bottom, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("ok", foreground="#1a8f2e")
        self.log.tag_config("bad", foreground="#c62828")
        self.log.tag_config("head", foreground="#1565c0", font=("Monospace", 9, "bold"))

        self.after(100, self.poll)
        self.after(300, self.refresh_setup)

    # ------------------------------------------------------------------ UI tabs
    def tab_setup(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="1. Setup")
        self.setup_info = tk.StringVar(value="Checking...")
        ttk.Label(f, textvariable=self.setup_info, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Install rclone", command=self.install_rclone).grid(row=1, column=0, pady=8, sticky="w")
        ttk.Button(f, text="Connect Google account", command=self.connect_google).grid(row=1, column=1, padx=6, sticky="w")
        ttk.Button(f, text="Re-check", command=self.refresh_setup).grid(row=1, column=2, sticky="w")

    def tab_download(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="2. Download project")
        ttk.Label(f, text="Drive folder link:").grid(row=0, column=0, sticky="w")
        self.dl_link = tk.StringVar()
        ttk.Entry(f, textvariable=self.dl_link, width=80).grid(row=0, column=1, columnspan=2, sticky="we")
        ttk.Button(f, text="Load subfolders", command=self.load_subfolders).grid(row=0, column=3, padx=4)
        ttk.Label(f, text="Save to:").grid(row=1, column=0, sticky="w")
        self.dl_dest = tk.StringVar(value=os.path.expanduser("~/metagenomics"))
        ttk.Entry(f, textvariable=self.dl_dest, width=80).grid(row=1, column=1, columnspan=2, sticky="we")
        ttk.Button(f, text="Browse", command=lambda: self.browse(self.dl_dest)).grid(row=1, column=3, padx=4)
        ttk.Label(f, text="Subfolders (Ctrl/Shift-click to select):").grid(row=2, column=0, sticky="nw", pady=4)
        self.dl_list = tk.Listbox(f, selectmode="extended", height=7, exportselection=False)
        self.dl_list.grid(row=2, column=1, columnspan=2, sticky="we", pady=4)
        side = ttk.Frame(f)
        side.grid(row=2, column=3, sticky="n")
        ttk.Button(side, text="Select all", command=lambda: self.dl_list.select_set(0, "end")).pack(fill="x")
        ttk.Button(side, text="Select none", command=lambda: self.dl_list.select_clear(0, "end")).pack(fill="x", pady=2)
        opt = ttk.Frame(f)
        opt.grid(row=3, column=1, columnspan=3, sticky="w")
        self.dl_quick = tk.BooleanVar()
        self.dl_jobs = tk.IntVar(value=8)
        self.dl_whole = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Quick verify (size only)", variable=self.dl_quick).pack(side="left")
        ttk.Label(opt, text="   Parallel downloads:").pack(side="left")
        ttk.Spinbox(opt, from_=1, to=16, width=4, textvariable=self.dl_jobs).pack(side="left")
        ttk.Checkbutton(opt, text="   Folder has no subfolders (download it as a whole)",
                        variable=self.dl_whole).pack(side="left")
        ttk.Button(f, text="Start download + verify", command=self.start_download).grid(row=4, column=1, sticky="w", pady=6)
        f.columnconfigure(1, weight=1)

    def tab_verify(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="3. Verify folder")
        ttk.Label(f, text="Local folder:").grid(row=0, column=0, sticky="w")
        self.v_local = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_local, width=80).grid(row=0, column=1, sticky="we")
        ttk.Button(f, text="Browse", command=lambda: self.browse(self.v_local)).grid(row=0, column=2, padx=4)
        ttk.Label(f, text="Matching Drive folder link:").grid(row=1, column=0, sticky="w")
        self.v_link = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_link, width=80).grid(row=1, column=1, sticky="we")
        self.v_quick = tk.BooleanVar()
        ttk.Checkbutton(f, text="Quick verify (size only)", variable=self.v_quick).grid(row=2, column=1, sticky="w")
        b = ttk.Frame(f)
        b.grid(row=3, column=1, sticky="w", pady=6)
        ttk.Button(b, text="Check only", command=lambda: self.start_verify(False)).pack(side="left")
        ttk.Button(b, text="Check + fix missing/corrupted", command=lambda: self.start_verify(True)).pack(side="left", padx=6)
        f.columnconfigure(1, weight=1)

    def tab_backup(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="4. Backup to server")
        ttk.Label(f, text="Local folder:").grid(row=0, column=0, sticky="w")
        self.b_src = tk.StringVar()
        ttk.Entry(f, textvariable=self.b_src, width=70).grid(row=0, column=1, sticky="we")
        ttk.Button(f, text="Browse", command=lambda: self.browse(self.b_src)).grid(row=0, column=2, padx=4)
        ttk.Label(f, text="Server (user@IP):").grid(row=1, column=0, sticky="w")
        self.b_host = tk.StringVar(value="user@192.168.1.10")
        ttk.Entry(f, textvariable=self.b_host, width=70).grid(row=1, column=1, sticky="we")
        ttk.Label(f, text="Folder on server:").grid(row=2, column=0, sticky="w")
        self.b_dst = tk.StringVar(value="~/backup/")
        ttk.Entry(f, textvariable=self.b_dst, width=70).grid(row=2, column=1, sticky="we")
        b = ttk.Frame(f)
        b.grid(row=3, column=1, sticky="w", pady=6)
        ttk.Button(b, text="Test connection + space", command=self.test_server).pack(side="left")
        ttk.Button(b, text="Start backup", command=self.start_backup).pack(side="left", padx=6)
        ttk.Button(b, text="Verify backup (MD5)", command=self.verify_backup).pack(side="left")
        ttk.Label(f, foreground="#666", text="Needs password-less SSH once:  ssh-keygen -t ed25519  then  ssh-copy-id user@IP  (in a terminal)").grid(
            row=4, column=1, sticky="w")
        f.columnconfigure(1, weight=1)

    # ------------------------------------------------------------- utilities
    def browse(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or os.path.expanduser("~"))
        if d:
            var.set(d)

    def write(self, text, tag=None):
        self.q.put(("log", text, tag))

    def set_status(self, text):
        self.q.put(("status", text, None))

    def poll(self):
        try:
            while True:
                kind, text, tag = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", text + "\n", tag)
                    self.log.see("end")
                    if self.logfile:
                        self.logfile.write(text + "\n"); self.logfile.flush()
                    m = PCT.search(text)
                    if m and ("Transferred" in text or "to-chk" in text or "ETA" in text):
                        self.pbar["value"] = int(m.group(1))
                elif kind == "status":
                    self.status.set(text)
                elif kind == "subfolders":
                    self.dl_list.delete(0, "end")
                    for d in text:
                        self.dl_list.insert("end", d)
                elif kind == "pbar":
                    self.pbar["value"] = text
                elif kind == "setup":
                    self.setup_info.set(text)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def run(self, cmd, quiet=False):
        """Run a command, stream its output to the log. Returns exit code."""
        if self.stop_flag:
            return -1
        if not quiet:
            self.write("$ " + " ".join(cmd), "head")
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, bufsize=1)
        except FileNotFoundError:
            self.write(f"Command not found: {cmd[0]}", "bad")
            return 127
        for line in self.proc.stdout:
            line = line.rstrip()
            if line and not (quiet and "NOTICE" in line):
                if "client_id" in line and "retired" in line:
                    continue
                self.write(line)
        self.proc.wait()
        rc = self.proc.returncode
        self.proc = None
        return rc

    def capture(self, cmd):
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def thread(self, target, *args):
        if self.busy:
            messagebox.showinfo("Busy", "A task is already running. Press Stop or wait.")
            return
        self.busy = True
        self.stop_flag = False
        self.pbar["value"] = 0

        def wrap():
            try:
                target(*args)
            except Exception as e:
                self.write(f"ERROR: {e}", "bad")
            finally:
                self.busy = False
                if self.logfile:
                    self.logfile.close(); self.logfile = None
                if self.stop_flag:
                    self.set_status("Stopped")
        threading.Thread(target=wrap, daemon=True).start()

    def stop(self):
        self.stop_flag = True
        if self.proc:
            self.proc.terminate()
        self.write("Stop requested - current transfer will resume next time you start it.", "bad")

    def need_rclone(self):
        if not shutil.which("rclone"):
            messagebox.showerror("rclone missing", "Go to tab 1 and click 'Install rclone'.")
            return False
        rc, out, _ = self.capture(["rclone", "listremotes"])
        if f"{REMOTE}:" not in out.split():
            messagebox.showerror("Not connected", "Go to tab 1 and click 'Connect Google account'.")
            return False
        return True

    def open_log(self, folder):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"metasync_{datetime.datetime.now():%Y%m%d_%H%M%S}.log")
        self.logfile = open(path, "w")
        self.write(f"Log file: {path}")

    # ------------------------------------------------------------------ setup
    def refresh_setup(self):
        lines = []
        rcl = shutil.which("rclone")
        if rcl:
            _, v, _ = self.capture(["rclone", "version"])
            lines.append("rclone:  installed  (" + v.splitlines()[0] + ")")
            _, r, _ = self.capture(["rclone", "listremotes"])
            lines.append("Google:  " + ("CONNECTED" if f"{REMOTE}:" in r.split() else "not connected - click 'Connect Google account'"))
        else:
            lines.append("rclone:  NOT installed - click 'Install rclone'")
        lines.append("rsync:   " + ("installed" if shutil.which("rsync") else "NOT installed (sudo apt install rsync)"))
        self.q.put(("setup", "\n".join(lines), None))

    def install_rclone(self):
        def job():
            self.set_status("Installing rclone (a password window will appear)...")
            rc = self.run(["pkexec", "bash", "-c", "curl -fsSL https://rclone.org/install.sh | bash"])
            self.write("rclone installed." if rc in (0, 3) else "Install failed - run in a terminal: "
                       "curl -fsSL https://rclone.org/install.sh | sudo bash", "ok" if rc in (0, 3) else "bad")
            self.set_status("Ready"); self.after(0, self.refresh_setup)
        self.thread(job)

    def connect_google(self):
        if not shutil.which("rclone"):
            messagebox.showerror("rclone missing", "Install rclone first."); return
        def job():
            self.set_status("A browser window will open - sign in and click Allow")
            rc = self.run(["rclone", "config", "create", REMOTE, "drive", "scope=drive.readonly"])
            self.write("Google account connected." if rc == 0 else "Connection failed.", "ok" if rc == 0 else "bad")
            self.set_status("Ready"); self.after(0, self.refresh_setup)
        self.thread(job)

    # --------------------------------------------------------------- core ops
    def rc_args(self, fid):
        return ["--drive-root-folder-id", fid]

    def copy(self, fid, sub, local, jobs, files_from=None):
        cmd = ["rclone", "copy", f"{REMOTE}:{sub}", local, *self.rc_args(fid),
               "--transfers", str(jobs), "--checkers", "16", "--drive-chunk-size", "128M",
               "--drive-acknowledge-abuse", "--retries", "10", "--low-level-retries", "20",
               "--tpslimit", "10", "--stats", "5s", "--stats-one-line", "-v"]
        if files_from:
            cmd += ["--files-from", files_from, "--ignore-times"]
        return self.run(cmd)

    def check(self, fid, sub, local, quick, repdir):
        os.makedirs(repdir, exist_ok=True)
        for n in ("OK", "MISSING", "CORRUPTED", "ERRORS", "EXTRA"):
            p = os.path.join(repdir, n + ".txt")
            if os.path.exists(p):
                os.remove(p)
        self.write("Verifying " + ("(size only)" if quick else "(MD5 - reads every local file, may take a while)") + "...")
        cmd = ["rclone", "check", f"{REMOTE}:{sub}", local, *self.rc_args(fid), "--one-way", "--checkers", "16",
               "--match", f"{repdir}/OK.txt", "--missing-on-dst", f"{repdir}/MISSING.txt",
               "--differ", f"{repdir}/CORRUPTED.txt", "--error", f"{repdir}/ERRORS.txt",
               "--stats", "10s", "--stats-one-line", "-q"]
        if quick:
            cmd.append("--size-only")
        self.run(cmd, quiet=True)
        self.run(["rclone", "check", f"{REMOTE}:{sub}", local, *self.rc_args(fid), "--size-only",
                  "--missing-on-src", f"{repdir}/EXTRA.txt", "-q"], quiet=True)
        r = {n: count_lines(os.path.join(repdir, n + ".txt")) for n in ("OK", "MISSING", "CORRUPTED", "ERRORS", "EXTRA")}
        tag = "ok" if r["MISSING"] + r["CORRUPTED"] + r["ERRORS"] == 0 else "bad"
        self.write(f"  OK: {r['OK']}   missing: {r['MISSING']}   corrupted: {r['CORRUPTED']}   "
                   f"errors: {r['ERRORS']}   extra local (ignored): {r['EXTRA']}", tag)
        for n in ("MISSING", "CORRUPTED", "ERRORS"):
            for line in read_lines(os.path.join(repdir, n + ".txt"))[:15]:
                self.write(f"    {n.lower()}: {line}", "bad")
        return r

    def sync_one(self, fid, sub, local, quick, jobs, repdir, fix=True, download_first=True):
        """download -> verify -> fix loop. Returns result dict."""
        os.makedirs(local, exist_ok=True)
        r = None
        for attempt in range(1, 4):
            if self.stop_flag:
                break
            if download_first or attempt > 1:
                self.write(f"[download pass {attempt}]", "head")
                if attempt > 1:
                    # delete corrupted copies so they are fetched fresh
                    for f in read_lines(os.path.join(repdir, "CORRUPTED.txt")):
                        try: os.remove(os.path.join(local, f))
                        except OSError: pass
                self.copy(fid, sub, local, jobs)
            if self.stop_flag:
                break
            r = self.check(fid, sub, local, quick, repdir)
            if r["MISSING"] + r["CORRUPTED"] + r["ERRORS"] == 0 or not fix:
                break
        return r

    # --------------------------------------------------------------- download
    def load_subfolders(self):
        if not self.need_rclone():
            return
        fid = folder_id(self.dl_link.get())
        if not fid:
            messagebox.showerror("Missing", "Paste a Drive folder link first."); return
        def job():
            self.set_status("Reading folder list from Drive...")
            rc, out, err = self.capture(["rclone", "lsf", f"{REMOTE}:", *self.rc_args(fid), "--dirs-only"])
            dirs = sorted([d.rstrip("/") for d in out.splitlines() if d.strip()],
                          key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])
            if rc != 0:
                self.write("Could not read folder: " + err.strip(), "bad")
            elif not dirs:
                self.write("No subfolders - tick 'Folder has no subfolders' and start.", "bad")
            else:
                self.write(f"Found {len(dirs)} subfolders: " + ", ".join(dirs), "ok")
            self.q.put(("subfolders", dirs, None))
            self.set_status("Ready - select subfolders and start")
        self.thread(job)

    def start_download(self):
        if not self.need_rclone():
            return
        fid = folder_id(self.dl_link.get())
        dest = os.path.expanduser(self.dl_dest.get().strip())
        if not fid or not dest:
            messagebox.showerror("Missing", "Fill in the Drive link and destination."); return
        whole = self.dl_whole.get()
        subs = [self.dl_list.get(i) for i in self.dl_list.curselection()]
        if not whole and not subs:
            messagebox.showerror("Nothing selected", "Click 'Load subfolders' and select some, "
                                 "or tick 'Folder has no subfolders'."); return
        quick, jobs = self.dl_quick.get(), self.dl_jobs.get()
        free = shutil.disk_usage(dest if os.path.exists(dest) else os.path.dirname(dest) or "/").free
        self.write(f"Free space at destination: {free/1e9:.0f} GB")

        def job():
            os.makedirs(dest, exist_ok=True)
            run_dir = os.path.join(dest, "_logs", f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}")
            self.open_log(run_dir)
            results = {}
            targets = [("", dest, "whole_folder")] if whole else [(s, os.path.join(dest, s), s) for s in subs]
            if not whole:  # loose files in project root
                self.run(["rclone", "copy", f"{REMOTE}:", dest, *self.rc_args(fid), "--max-depth", "1", "-q"], quiet=True)
            for i, (sub, local, name) in enumerate(targets, 1):
                if self.stop_flag:
                    break
                self.set_status(f"[{i}/{len(targets)}] {name}")
                self.write(f"\n=========== {name} ===========", "head")
                r = self.sync_one(fid, sub, local, quick, jobs, os.path.join(run_dir, name))
                results[name] = r
            self.summary(results)
        self.thread(job)

    def summary(self, results):
        self.write("\n================ SUMMARY ================", "head")
        bad = False
        for name, r in results.items():
            if r is None:
                self.write(f"{name:35s} not finished", "bad"); bad = True; continue
            prob = r["MISSING"] + r["CORRUPTED"] + r["ERRORS"]
            bad |= prob > 0
            self.write(f"{name:35s} " + (f"OK ({r['OK']} files)" if prob == 0 else
                       f"PROBLEM: {r['MISSING']} missing, {r['CORRUPTED']} corrupted, {r['ERRORS']} errors"),
                       "ok" if prob == 0 else "bad")
        if results and not bad and not self.stop_flag:
            self.write("ALL DOWNLOADED AND VERIFIED.", "ok"); self.set_status("Done - all verified")
            self.q.put(("pbar", 100, None))
        elif not self.stop_flag:
            self.write("Some problems remain - just press Start again to resume/fix.", "bad")
            self.set_status("Finished with problems")

    # ----------------------------------------------------------------- verify
    def start_verify(self, fix):
        if not self.need_rclone():
            return
        local = os.path.expanduser(self.v_local.get().strip())
        fid = folder_id(self.v_link.get())
        if not os.path.isdir(local) or not fid:
            messagebox.showerror("Missing", "Choose an existing local folder and paste its Drive link."); return
        quick = self.v_quick.get()

        def job():
            repdir = os.path.join(os.path.dirname(local.rstrip("/")), "_check_reports",
                                  f"{os.path.basename(local.rstrip('/'))}_{datetime.datetime.now():%Y%m%d_%H%M%S}")
            self.open_log(repdir)
            self.set_status("Checking " + local)
            self.write(f"\n=========== Verify {local} ===========", "head")
            r = self.sync_one(fid, "", local, quick, 8, repdir, fix=fix, download_first=False)
            self.summary({os.path.basename(local.rstrip('/')): r})
            self.write(f"Reports: {repdir}")
        self.thread(job)

    # ----------------------------------------------------------------- backup
    def ssh(self, host):
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", host]

    def test_server(self):
        host = self.b_host.get().strip()
        dst = self.b_dst.get().strip()
        def job():
            self.set_status("Testing connection...")
            rc = self.run(self.ssh(host) + [f"mkdir -p {dst} && echo CONNECTED && df -h {dst}"])
            if rc != 0:
                self.write("Cannot log in without password. In a terminal run:\n"
                           f"  ssh-keygen -t ed25519\n  ssh-copy-id {host}", "bad")
            if self.b_src.get().strip():
                _, out, _ = self.capture(["du", "-sh", os.path.expanduser(self.b_src.get().strip())])
                self.write("Size of local folder: " + out.split("\t")[0])
            self.set_status("Ready")
        self.thread(job)

    def backup_paths(self):
        src = os.path.expanduser(self.b_src.get().strip()).rstrip("/")
        host, dst = self.b_host.get().strip(), self.b_dst.get().strip().rstrip("/")
        if not os.path.isdir(src) or "@" not in host or not dst:
            messagebox.showerror("Missing", "Fill in local folder, user@IP and server folder."); return None
        return src, f"{host}:{dst}/{os.path.basename(src)}/"

    def start_backup(self):
        p = self.backup_paths()
        if not p:
            return
        src, target = p
        def job():
            self.set_status("Backing up...")
            host = self.b_host.get().strip()
            remote_dir = target.split(":", 1)[1]
            self.run(self.ssh(host) + [f"mkdir -p {remote_dir}"], quiet=True)
            rc = self.run(["rsync", "-ah", "--partial", "--info=progress2,stats1", "-e",
                           "ssh -o BatchMode=yes", src + "/", target])
            if rc == 0:
                self.write("Copy finished. Now press 'Verify backup (MD5)'.", "ok"); self.set_status("Backup copied")
            elif not self.stop_flag:
                self.write("Backup failed or incomplete - press Start backup again to resume.", "bad")
                self.set_status("Backup problem")
        self.thread(job)

    def verify_backup(self):
        p = self.backup_paths()
        if not p:
            return
        src, target = p
        def job():
            self.set_status("Verifying backup by checksum (reads all files on both servers)...")
            r = subprocess.run(["rsync", "-an", "--checksum", "--out-format=%n", "-e", "ssh -o BatchMode=yes",
                                src + "/", target], capture_output=True, text=True)
            diff = [l for l in r.stdout.splitlines() if l.strip() and not l.endswith("/")]
            if r.returncode != 0:
                self.write("Verify failed: " + r.stderr.strip(), "bad"); self.set_status("Verify failed")
            elif diff:
                self.write(f"{len(diff)} file(s) differ or are missing on the server:", "bad")
                for d in diff[:30]:
                    self.write("   " + d, "bad")
                self.write("Press 'Start backup' again to fix them.", "bad"); self.set_status("Backup incomplete")
            else:
                self.write("BACKUP VERIFIED - every file is identical on both servers.", "ok")
                self.set_status("Backup verified"); self.q.put(("pbar", 100, None))
        self.thread(job)


def install_shortcut():
    path = os.path.realpath(__file__)
    d = os.path.expanduser("~/.local/share/applications")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "metasync.desktop"), "w") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name=MetaSync
Comment=Download, verify and back up Google Drive data
Exec={sys.executable} {path}
Icon=folder-download
Terminal=false
Categories=Utility;Science;
""")
    print("Shortcut installed: search 'MetaSync' in the Ubuntu app menu.")


if __name__ == "__main__":
    if "--install" in sys.argv:
        install_shortcut()
    else:
        App().mainloop()
