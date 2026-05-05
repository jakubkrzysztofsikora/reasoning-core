"""Audit-log rotation helpers. Split from audit_log to keep cyclomatic low."""
from __future__ import annotations

import datetime as _dt
import gzip
import os
import shutil
from pathlib import Path

_RETENTION_DAYS = int(os.environ.get("RC_AUDIT_RETENTION_DAYS", "90"))
_DISK_CAP_BYTES = int(os.environ.get("RC_AUDIT_CAP_BYTES", str(5 * 1024 * 1024 * 1024)))


def _gzip_one(jf: Path) -> None:
    gz = jf.with_suffix(".jsonl.gz")
    if gz.exists():
        return
    try:
        with open(jf, "rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        jf.unlink()
    except OSError:
        pass


def _stat_or_none(f: Path):
    try:
        st = f.stat()
        mtime = _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc)
        return mtime, st.st_size
    except OSError:
        return None


def _evict_old(root: Path, cutoff: _dt.datetime) -> list:
    survivors: list = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            info = _stat_or_none(f)
            if info is None:
                continue
            mtime, size = info
            if mtime < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass
            else:
                survivors.append((mtime.timestamp(), f, size))
    return survivors


def _enforce_cap(survivors: list) -> None:
    total = sum(s for _, _, s in survivors)
    if total <= _DISK_CAP_BYTES:
        return
    for _, f, sz in sorted(survivors):
        try:
            f.unlink()
            total -= sz
        except OSError:
            pass
        if total <= _DISK_CAP_BYTES:
            return


def rotate(audit_root: str, today: str) -> None:
    """Gzip prior days, evict expired, enforce disk cap. Never raises."""
    try:
        root = Path(audit_root)
        if not root.exists():
            return
        for sub in root.iterdir():
            if sub.is_dir() and sub.name != today:
                for jf in sub.glob("*.jsonl"):
                    _gzip_one(jf)
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=_RETENTION_DAYS)
        survivors = _evict_old(root, cutoff)
        _enforce_cap(survivors)
    except Exception:  # noqa: BLE001
        pass
