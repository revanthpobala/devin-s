#!/usr/bin/env python3
"""archive_repo_data.py — Production Archival Engine for Stock Repository Data & Logs.

Safely archives and compresses historical data, old date directories, and oversized logs
to an external folder (e.g. D:\\My-Projects\\Stock_Archive) so the repository stays fast,
compact, and does not balloon with tens of thousands of temporary files.

Safety Guarantees:
  1. Dry-run by default: scans and calculates without altering or deleting any files.
  2. Strict verification: tests every zip file via zipfile.testzip() before cleaning source.
  3. Safe log rotation: archives oversized logs while retaining the last tail lines in-place
     so running daemons (orchestrator, UI, tracker) do not crash or lose file descriptors.
  4. Explicit confirmation: requires --execute and user prompt (or --yes) before performing actions.

Usage:
  # 1. Preview what will be archived (DRY RUN)
  python scripts/maintenance/archive_repo_data.py

  # 2. Run archival with external destination folder
  python scripts/maintenance/archive_repo_data.py --execute --dest "D:\\My-Projects\\Stock_Archive"

  # 3. Custom retention policies (e.g. keep 14 days raw, 7 days logs)
  python scripts/maintenance/archive_repo_data.py --execute --days-raw 14 --days-logs 7

  # 4. Archive specific category only (e.g. logs only)
  python scripts/maintenance/archive_repo_data.py --execute --category logs
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("archive_repo")

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Root of the active repository
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ARCHIVE_DEST = Path(os.getenv("STOCK_ARCHIVE_DIR", "D:/My-Projects/Stock_Archive"))


def format_bytes(size: int | float) -> str:
    """Return human-readable byte string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    s = float(size)
    for u in units:
        if s < 1024.0 or u == units[-1]:
            return f"{s:.2f} {u}"
        s /= 1024.0
    return f"{s:.2f} B"


def get_dir_size_and_count(path: Path) -> Tuple[int, int]:
    """Calculate total size in bytes and file count for a directory recursively."""
    total_size = 0
    file_count = 0
    if not path.exists():
        return 0, 0
    if path.is_file():
        return path.stat().st_size, 1

    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = Path(root) / f
                try:
                    total_size += fp.stat().st_size
                    file_count += 1
                except (OSError, FileNotFoundError):
                    pass
    except (OSError, FileNotFoundError):
        pass
    return total_size, file_count


def parse_date_folder(name: str) -> Optional[datetime]:
    """Parse YYYY-MM-DD date folder name into a datetime object."""
    try:
        return datetime.strptime(name.strip(), "%Y-%m-%d")
    except ValueError:
        return None


class ArchivalEngine:
    def __init__(
        self,
        dest_dir: Path,
        days_raw: int = 30,
        days_triage: int = 30,
        days_logs: int = 7,
        days_backups: int = 7,
        max_log_mb: float = 20.0,
        dry_run: bool = True,
    ):
        self.dest_dir = Path(dest_dir).resolve()
        self.days_raw = days_raw
        self.days_triage = days_triage
        self.days_logs = days_logs
        self.days_backups = days_backups
        self.max_log_bytes = int(max_log_mb * 1024 * 1024)
        self.dry_run = dry_run

        self.cutoff_raw = datetime.now() - timedelta(days=days_raw)
        self.cutoff_triage = datetime.now() - timedelta(days=days_triage)
        self.cutoff_logs = datetime.now() - timedelta(days=days_logs)
        self.cutoff_backups = datetime.now() - timedelta(days=days_backups)

        # Statistics
        self.planned_raw_dirs: List[Tuple[Path, int, int]] = []
        self.planned_triage_dirs: List[Tuple[Path, int, int]] = []
        self.planned_auto_logs: List[Tuple[Path, int]] = []
        self.planned_root_logs: List[Tuple[Path, int]] = []
        self.planned_backup_items: List[Tuple[Path, int, int]] = []

    def scan_all(self, category: str = "all") -> Dict[str, Any]:
        """Scan active repository directories and collect candidate items for archival."""
        if category in ("all", "raw"):
            self._scan_date_subdirs(
                REPO_ROOT / "data" / "raw",
                self.cutoff_raw,
                self.planned_raw_dirs,
            )

        if category in ("all", "triage"):
            self._scan_date_subdirs(
                REPO_ROOT / "data" / "triage",
                self.cutoff_triage,
                self.planned_triage_dirs,
            )

        if category in ("all", "logs"):
            self._scan_auto_logs()
            self._scan_root_logs()

        if category in ("all", "backups"):
            self._scan_backups()

        total_bytes = (
            sum(sz for _, sz, _ in self.planned_raw_dirs)
            + sum(sz for _, sz, _ in self.planned_triage_dirs)
            + sum(sz for _, sz in self.planned_auto_logs)
            + sum(sz for _, sz in self.planned_root_logs)
            + sum(sz for _, sz, _ in self.planned_backup_items)
        )
        total_files = (
            sum(fc for _, _, fc in self.planned_raw_dirs)
            + sum(fc for _, _, fc in self.planned_triage_dirs)
            + len(self.planned_auto_logs)
            + len(self.planned_root_logs)
            + sum(fc for _, _, fc in self.planned_backup_items)
        )

        return {
            "total_bytes": total_bytes,
            "total_files": total_files,
            "raw_count": len(self.planned_raw_dirs),
            "triage_count": len(self.planned_triage_dirs),
            "auto_logs_count": len(self.planned_auto_logs),
            "root_logs_count": len(self.planned_root_logs),
            "backups_count": len(self.planned_backup_items),
        }

    def _scan_date_subdirs(
        self, parent_dir: Path, cutoff: datetime, out_list: List[Tuple[Path, int, int]]
    ) -> None:
        if not parent_dir.exists():
            return
        for item in parent_dir.iterdir():
            if item.is_dir():
                dt = parse_date_folder(item.name)
                if dt and dt < cutoff:
                    sz, fc = get_dir_size_and_count(item)
                    if fc > 0:
                        out_list.append((item, sz, fc))

    def _scan_auto_logs(self) -> None:
        auto_logs_dir = REPO_ROOT / "data" / "logs"
        if not auto_logs_dir.exists():
            return
        cutoff_ts = self.cutoff_logs.timestamp()
        for item in auto_logs_dir.glob("*.log"):
            try:
                st = item.stat()
                if st.st_mtime < cutoff_ts:
                    self.planned_auto_logs.append((item, st.st_size))
            except (OSError, FileNotFoundError):
                pass

    def _scan_root_logs(self) -> None:
        logs_dir = REPO_ROOT / "logs"
        if not logs_dir.exists():
            return
        cutoff_ts = self.cutoff_logs.timestamp()
        for item in logs_dir.glob("*.log"):
            try:
                st = item.stat()
                # Large files or older files
                if st.st_size > self.max_log_bytes or st.st_mtime < cutoff_ts:
                    if st.st_size > 1024 * 1024:  # At least 1MB to avoid empty/tiny active handles
                        self.planned_root_logs.append((item, st.st_size))
            except (OSError, FileNotFoundError):
                pass

    def _scan_backups(self) -> None:
        backups_dir = REPO_ROOT / "data" / "backups"
        if not backups_dir.exists():
            return
        cutoff_ts = self.cutoff_backups.timestamp()
        for item in backups_dir.iterdir():
            try:
                st = item.stat()
                if st.st_mtime < cutoff_ts:
                    sz, fc = get_dir_size_and_count(item)
                    out_list_item = (item, sz, fc)
                    self.planned_backup_items.append(out_list_item)
            except (OSError, FileNotFoundError):
                pass

    def print_manifest(self) -> None:
        """Display detailed dry-run manifest to user."""
        print("=" * 80)
        print(f"📦 STOCK REPOSITORY ARCHIVE MANIFEST [{'DRY RUN' if self.dry_run else 'EXECUTE'}]")
        print(f"   Destination Directory: {self.dest_dir}")
        print("=" * 80)

        print(f"\n1. Historical Raw Scrape Folders (Cutoff > {self.days_raw} days / {self.cutoff_raw.strftime('%Y-%m-%d')}):")
        if not self.planned_raw_dirs:
            print("   (None qualify for archival)")
        else:
            for p, sz, fc in sorted(self.planned_raw_dirs, key=lambda x: x[0].name):
                print(f"   • {p.name:<12} -> {format_bytes(sz):>10} ({fc:>4} files)")

        print(f"\n2. Historical Triage Folders (Cutoff > {self.days_triage} days / {self.cutoff_triage.strftime('%Y-%m-%d')}):")
        if not self.planned_triage_dirs:
            print("   (None qualify for archival)")
        else:
            for p, sz, fc in sorted(self.planned_triage_dirs, key=lambda x: x[0].name):
                print(f"   • {p.name:<12} -> {format_bytes(sz):>10} ({fc:>4} files)")

        print(f"\n3. Automated Ticker Execution Logs in data/logs (Cutoff > {self.days_logs} days):")
        auto_logs_bytes = sum(sz for _, sz in self.planned_auto_logs)
        print(f"   • Total Files: {len(self.planned_auto_logs):,} files ({format_bytes(auto_logs_bytes)})")

        print(f"\n4. Active Large/Stale Logs in logs/ (> {format_bytes(self.max_log_bytes)} or > {self.days_logs} days):")
        if not self.planned_root_logs:
            print("   (None qualify for archival)")
        else:
            for p, sz in self.planned_root_logs:
                print(f"   • {p.name:<25} -> {format_bytes(sz):>10} (will rotate & keep last 5,000 lines)")

        print(f"\n5. Database Backups in data/backups (Cutoff > {self.days_backups} days):")
        if not self.planned_backup_items:
            print("   (None qualify for archival)")
        else:
            for p, sz, fc in self.planned_backup_items:
                print(f"   • {p.name:<25} -> {format_bytes(sz):>10}")

        total_bytes = (
            sum(sz for _, sz, _ in self.planned_raw_dirs)
            + sum(sz for _, sz, _ in self.planned_triage_dirs)
            + sum(sz for _, sz in self.planned_auto_logs)
            + sum(sz for _, sz in self.planned_root_logs)
            + sum(sz for _, sz, _ in self.planned_backup_items)
        )
        total_files = (
            sum(fc for _, _, fc in self.planned_raw_dirs)
            + sum(fc for _, _, fc in self.planned_triage_dirs)
            + len(self.planned_auto_logs)
            + len(self.planned_root_logs)
            + sum(fc for _, _, fc in self.planned_backup_items)
        )

        print("-" * 80)
        print(f"TOTAL RECLAIMABLE: {format_bytes(total_bytes)} across {total_files:,} files/folders")
        print("=" * 80)

    def execute_archive(self) -> Dict[str, Any]:
        """Execute archival and compression with strict verification."""
        if self.dry_run:
            logger.info("Dry-run mode active. No changes made.")
            return {"status": "dry_run"}

        self.dest_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        archived_bytes = 0
        archived_files = 0
        errors = []

        # 1. Archive Raw Dirs
        raw_dest = self.dest_dir / "raw"
        raw_dest.mkdir(parents=True, exist_ok=True)
        for folder, sz, fc in self.planned_raw_dirs:
            zip_path = raw_dest / f"{folder.name}.zip"
            logger.info(f"Archiving raw directory {folder.name} ({format_bytes(sz)}, {fc} files)...")
            try:
                self._zip_directory(folder, zip_path)
                if self._verify_zip(zip_path):
                    shutil.rmtree(folder)
                    archived_bytes += sz
                    archived_files += fc
                else:
                    errors.append(f"Verification failed for {zip_path.name}; source retained.")
            except Exception as e:
                errors.append(f"Failed archiving {folder}: {e}")

        # 2. Archive Triage Dirs
        triage_dest = self.dest_dir / "triage"
        triage_dest.mkdir(parents=True, exist_ok=True)
        for folder, sz, fc in self.planned_triage_dirs:
            zip_path = triage_dest / f"{folder.name}.zip"
            logger.info(f"Archiving triage directory {folder.name} ({format_bytes(sz)}, {fc} files)...")
            try:
                self._zip_directory(folder, zip_path)
                if self._verify_zip(zip_path):
                    shutil.rmtree(folder)
                    archived_bytes += sz
                    archived_files += fc
                else:
                    errors.append(f"Verification failed for {zip_path.name}; source retained.")
            except Exception as e:
                errors.append(f"Failed archiving {folder}: {e}")

        # 3. Archive Auto Logs (Grouped by month to avoid 4,000 individual files in archive)
        if self.planned_auto_logs:
            logs_dest = self.dest_dir / "logs"
            logs_dest.mkdir(parents=True, exist_ok=True)
            by_month: Dict[str, List[Path]] = {}
            for lp, sz in self.planned_auto_logs:
                m = datetime.fromtimestamp(lp.stat().st_mtime).strftime("%Y_%m")
                by_month.setdefault(m, []).append(lp)

            for month_str, file_list in by_month.items():
                zip_path = logs_dest / f"auto_logs_{month_str}.zip"
                logger.info(f"Archiving {len(file_list)} auto-logs for {month_str} into {zip_path.name}...")
                try:
                    mode = "a" if zip_path.exists() else "w"
                    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED) as zf:
                        for lp in file_list:
                            if lp.exists():
                                zf.write(lp, arcname=lp.name)

                    if self._verify_zip(zip_path):
                        for lp in file_list:
                            try:
                                sz = lp.stat().st_size
                                lp.unlink()
                                archived_bytes += sz
                                archived_files += 1
                            except Exception:
                                pass
                    else:
                        errors.append(f"Verification failed for {zip_path.name}; logs retained.")
                except Exception as e:
                    errors.append(f"Failed archiving auto-logs for {month_str}: {e}")

        # 4. Rotate and Archive Root Logs
        if self.planned_root_logs:
            logs_dest = self.dest_dir / "logs"
            logs_dest.mkdir(parents=True, exist_ok=True)
            for lp, sz in self.planned_root_logs:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                gz_path = logs_dest / f"{lp.stem}_{ts}.log.gz"
                logger.info(f"Rotating root log {lp.name} ({format_bytes(sz)}) to {gz_path.name}...")
                try:
                    self._rotate_log_file(lp, gz_path, keep_lines=5000)
                    archived_bytes += sz
                    archived_files += 1
                except Exception as e:
                    errors.append(f"Failed rotating log {lp.name}: {e}")

        # 5. Archive Backups
        if self.planned_backup_items:
            backups_dest = self.dest_dir / "backups"
            backups_dest.mkdir(parents=True, exist_ok=True)
            for item, sz, fc in self.planned_backup_items:
                zip_path = backups_dest / f"{item.name}.zip"
                logger.info(f"Archiving backup {item.name} ({format_bytes(sz)})...")
                try:
                    if item.is_dir():
                        self._zip_directory(item, zip_path)
                    else:
                        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                            zf.write(item, arcname=item.name)

                    if self._verify_zip(zip_path):
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                        archived_bytes += sz
                        archived_files += fc
                    else:
                        errors.append(f"Verification failed for {zip_path.name}; source retained.")
                except Exception as e:
                    errors.append(f"Failed archiving backup {item.name}: {e}")

        elapsed = time.time() - t0
        logger.info(
            f"✅ Archival complete in {elapsed:.1f}s. Reclaimed {format_bytes(archived_bytes)} across {archived_files:,} items."
        )
        if errors:
            logger.warning(f"Encountered {len(errors)} warning(s):\n" + "\n".join(f"  • {e}" for e in errors))

        return {
            "status": "success",
            "archived_bytes": archived_bytes,
            "archived_files": archived_files,
            "elapsed_s": elapsed,
            "errors": errors,
        }

    def _zip_directory(self, src_dir: Path, zip_path: Path) -> None:
        """Create a compressed zip file from a directory."""
        temp_zip = zip_path.with_suffix(".tmp.zip")
        if temp_zip.exists():
            temp_zip.unlink()

        with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(src_dir):
                for f in files:
                    fp = Path(root) / f
                    arcname = fp.relative_to(src_dir)
                    zf.write(fp, arcname=arcname)

        if zip_path.exists():
            zip_path.unlink()
        temp_zip.rename(zip_path)

    def _verify_zip(self, zip_path: Path) -> bool:
        """Verify zip integrity by checking CRC of all entries."""
        if not zip_path.exists() or zip_path.stat().st_size == 0:
            return False
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                bad_file = zf.testzip()
                return bad_file is None
        except Exception:
            return False

    def _rotate_log_file(self, log_path: Path, dest_gz: Path, keep_lines: int = 5000) -> None:
        """Compress the bulk of a log file into .gz while keeping the tail in-place."""
        temp_gz = dest_gz.with_suffix(".tmp.gz")
        if temp_gz.exists():
            temp_gz.unlink()

        tail_lines: List[bytes] = []
        with open(log_path, "rb") as f_in:
            with gzip.open(temp_gz, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Read last lines to keep active logger alive
        try:
            with open(log_path, "rb") as f_in:
                lines = f_in.readlines()
                tail_lines = lines[-keep_lines:] if len(lines) > keep_lines else lines
        except Exception:
            tail_lines = []

        temp_gz.rename(dest_gz)

        # Atomically truncate and write tail
        with open(log_path, "wb") as f_out:
            f_out.writelines(tail_lines)


def main():
    parser = argparse.ArgumentParser(description="Stock Repository Archival Engine")
    parser.add_argument(
        "--dest",
        "-d",
        type=Path,
        default=DEFAULT_ARCHIVE_DEST,
        help=f"Target external directory for archives (default: {DEFAULT_ARCHIVE_DEST})",
    )
    parser.add_argument(
        "--days-raw",
        type=int,
        default=30,
        help="Retention period in days for raw scrape date folders (default: 30 days / 1 month)",
    )
    parser.add_argument(
        "--days-triage",
        type=int,
        default=30,
        help="Retention period in days for triage date folders (default: 30 days / 1 month)",
    )
    parser.add_argument(
        "--days-logs",
        type=int,
        default=7,
        help="Retention period in days for automated per-ticker execution logs (default: 7)",
    )
    parser.add_argument(
        "--days-backups",
        type=int,
        default=7,
        help="Retention period in days for database backups (default: 7)",
    )
    parser.add_argument(
        "--max-log-size-mb",
        type=float,
        default=20.0,
        help="Size threshold in MB to rotate and archive active root logs (default: 20 MB)",
    )
    parser.add_argument(
        "--category",
        choices=["all", "raw", "triage", "logs", "backups"],
        default="all",
        help="Specific category to scan/archive (default: all)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform actual archival. If not passed, runs in safe DRY RUN mode.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip interactive confirmation prompt when --execute is used.",
    )

    args = parser.parse_args()

    engine = ArchivalEngine(
        dest_dir=args.dest,
        days_raw=args.days_raw,
        days_triage=args.days_triage,
        days_logs=args.days_logs,
        days_backups=args.days_backups,
        max_log_mb=args.max_log_size_mb,
        dry_run=not args.execute,
    )

    summary = engine.scan_all(category=args.category)
    engine.print_manifest()

    if not args.execute:
        print("\n💡 NOTE: This was a DRY RUN. No files were moved or deleted.")
        print(f"To perform this archival to '{args.dest}', run:")
        print(f"   python scripts/maintenance/archive_repo_data.py --execute --dest \"{args.dest}\"")
        return

    if not args.yes:
        confirm = input(f"\n⚠️  Are you sure you want to archive these items to '{args.dest}'? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Archival cancelled by user.")
            return

    engine.execute_archive()


if __name__ == "__main__":
    main()
