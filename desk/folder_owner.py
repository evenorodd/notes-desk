"""In Docker, run as whoever owns the project folder, not as root."""

import os
from pathlib import Path


def run_as_folder_owner(app_dir):
    """Containers start as root, and so do commands run with `docker compose
    exec`, so files written into the shared folders (feeds/, reports/, export/)
    would belong to root on the host: you couldn't edit a price file, and runs
    without Docker would be blocked. Switch to whoever owns the project folder instead.
    On Docker Desktop (Mac, Windows) the folder usually shows as root's; then
    nothing changes, and nothing needs to."""
    if os.geteuid() != 0:
        return
    st = (app_dir / "feeds").stat()
    uid, gid = st.st_uid, st.st_gid
    if uid == 0:
        return
    # Docker creates missing folders as root, and runs before this fix could
    # have left root-owned files in any of them.
    for name in ("feeds", "reports", "export"):
        folder = app_dir / name
        folder.mkdir(exist_ok=True)
        for p in [folder, *folder.rglob("*")]:
            try:
                os.chown(p, uid, gid)
            except OSError:
                pass
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def in_docker():
    if os.environ.get("NOTES_DESK_DOCKER"):
        run_as_folder_owner(Path(__file__).resolve().parent.parent)
