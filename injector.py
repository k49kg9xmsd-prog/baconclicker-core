from __future__ import annotations

import os
import plistlib
import shutil
import stat
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
CPU_TYPE_ARM64 = 0x0100000C

LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_SEGMENT_64 = 0x19


class InjectError(RuntimeError):
    pass


def align8(n: int) -> int:
    return (n + 7) & ~7


def _dylib_command(path: str) -> bytes:
    raw = path.encode("utf-8") + b"\0"
    cmdsize = align8(24 + len(raw))
    out = bytearray(cmdsize)
    # dylib_command + dylib{name.offset, timestamp, current_version, compatibility_version}
    struct.pack_into("<IIIIII", out, 0, LC_LOAD_DYLIB, cmdsize, 24, 2, 0, 0)
    out[24 : 24 + len(raw)] = raw
    return bytes(out)


@dataclass
class Slice:
    offset: int
    size: int


def _arm64_slice(data: bytes) -> Slice:
    if len(data) < 4:
        raise InjectError("主程式不是有效的 Mach-O 檔案。")

    # Thin little-endian arm64 Mach-O.
    magic_le = struct.unpack_from("<I", data, 0)[0]
    if magic_le == MH_MAGIC_64:
        cputype = struct.unpack_from("<I", data, 4)[0]
        if cputype != CPU_TYPE_ARM64:
            raise InjectError(f"目前只支援 arm64；偵測到 CPU type 0x{cputype:08X}。")
        return Slice(0, len(data))

    # Fat headers are big-endian on disk.
    magic_be = struct.unpack_from(">I", data, 0)[0]
    if magic_be not in (FAT_MAGIC, FAT_MAGIC_64):
        raise InjectError("目前只支援 64-bit arm64 Mach-O / Universal Mach-O。")

    nfat = struct.unpack_from(">I", data, 4)[0]
    off = 8
    is64 = magic_be == FAT_MAGIC_64
    for _ in range(nfat):
        if is64:
            if off + 32 > len(data):
                break
            cputype, _cpusub, slice_off, slice_size, _align, _reserved = struct.unpack_from(">IIQQII", data, off)
            off += 32
        else:
            if off + 20 > len(data):
                break
            cputype, _cpusub, slice_off, slice_size, _align = struct.unpack_from(">IIIII", data, off)
            off += 20
        if cputype == CPU_TYPE_ARM64:
            if slice_off + slice_size > len(data):
                raise InjectError("Universal Mach-O 的 arm64 slice 尺寸無效。")
            return Slice(int(slice_off), int(slice_size))

    raise InjectError("Universal Mach-O 內找不到 arm64 slice。")


def _parse_load_commands(data: bytes, sl: Slice):
    base = sl.offset
    if struct.unpack_from("<I", data, base)[0] != MH_MAGIC_64:
        raise InjectError("arm64 slice 不是支援的 64-bit Mach-O。")

    ncmds, sizeofcmds = struct.unpack_from("<II", data, base + 16)
    cursor = base + 32
    commands: list[tuple[int, int, int]] = []
    first_section_offset: int | None = None

    for i in range(ncmds):
        if cursor + 8 > base + sl.size:
            raise InjectError(f"Mach-O load command #{i} 超出檔案範圍。")
        cmd, cmdsize = struct.unpack_from("<II", data, cursor)
        if cmdsize < 8 or cursor + cmdsize > base + sl.size:
            raise InjectError(f"Mach-O load command #{i} 結構異常。")
        commands.append((cursor, cmd, cmdsize))

        if cmd == LC_SEGMENT_64:
            nsects = struct.unpack_from("<I", data, cursor + 64)[0]
            sec = cursor + 72
            for _ in range(nsects):
                if sec + 80 > cursor + cmdsize:
                    raise InjectError("Mach-O section table 結構異常。")
                size = struct.unpack_from("<Q", data, sec + 40)[0]
                fileoff = struct.unpack_from("<I", data, sec + 48)[0]
                if fileoff and size:
                    abs_off = base + fileoff
                    if first_section_offset is None or abs_off < first_section_offset:
                        first_section_offset = abs_off
                sec += 80
        cursor += cmdsize

    return ncmds, sizeofcmds, commands, first_section_offset


def _loaded_dylibs(data: bytes, sl: Slice) -> set[str]:
    _n, _sz, commands, _first = _parse_load_commands(data, sl)
    names: set[str] = set()
    dylib_cmds = {LC_LOAD_DYLIB, 0x18, 0x1F, 0x80000018, 0x8000001F}
    for off, cmd, cmdsize in commands:
        if cmd not in dylib_cmds:
            continue
        nameoff = struct.unpack_from("<I", data, off + 8)[0]
        start = off + nameoff
        end = data.find(b"\0", start, off + cmdsize)
        if end < 0:
            end = off + cmdsize
        names.add(data[start:end].decode("utf-8", "replace"))
    return names


def add_load_dylib(executable: Path, load_path: str) -> bool:
    data = bytearray(executable.read_bytes())
    sl = _arm64_slice(data)
    existing = _loaded_dylibs(data, sl)
    if load_path in existing:
        return False

    ncmds, sizeofcmds, _commands, first_section = _parse_load_commands(data, sl)
    if first_section is None:
        raise InjectError("無法判斷主程式 Mach-O 的第一個 section 位置。")

    cmd = _dylib_command(load_path)
    insert_at = sl.offset + 32 + sizeofcmds
    available = first_section - insert_at
    if len(cmd) > available:
        raise InjectError(
            f"主程式 Mach-O 沒有足夠的 load-command 空間（需要 {len(cmd)} bytes，目前 {available} bytes）。"
        )
    # Refuse to destroy non-padding bytes.
    if any(data[insert_at : insert_at + len(cmd)]):
        raise InjectError("主程式 load-command 後方不是空白 padding，為避免損壞已停止注入。")

    data[insert_at : insert_at + len(cmd)] = cmd
    struct.pack_into("<I", data, sl.offset + 16, ncmds + 1)
    struct.pack_into("<I", data, sl.offset + 20, sizeofcmds + len(cmd))
    executable.write_bytes(data)
    return True


def patch_dylib_id(dylib: Path, new_id: str) -> bool:
    data = bytearray(dylib.read_bytes())
    sl = _arm64_slice(data)
    _n, _sz, commands, _first = _parse_load_commands(data, sl)
    for off, cmd, cmdsize in commands:
        if cmd != LC_ID_DYLIB:
            continue
        nameoff = struct.unpack_from("<I", data, off + 8)[0]
        capacity = cmdsize - nameoff
        raw = new_id.encode("utf-8") + b"\0"
        if len(raw) > capacity:
            return False
        start = off + nameoff
        data[start : off + cmdsize] = raw + b"\0" * (capacity - len(raw))
        dylib.write_bytes(data)
        return True
    return False


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if target != dest and dest not in target.parents:
            raise InjectError("IPA 內含不安全的 ZIP 路徑。")
        zf.extract(info, dest)
        # Restore UNIX mode when present.
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode:
            try:
                os.chmod(target, mode)
            except OSError:
                pass


def _zip_tree(root: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                # Explicit dirs are optional; adding them helps preserve structure.
                zi = zipfile.ZipInfo(rel.rstrip("/") + "/")
                zi.external_attr = (stat.S_IFDIR | 0o755) << 16
                zf.writestr(zi, b"")
                continue
            st = path.stat()
            zi = zipfile.ZipInfo.from_file(path, arcname=rel)
            zi.external_attr = (st.st_mode & 0xFFFF) << 16
            with path.open("rb") as f:
                zf.writestr(zi, f.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)


@dataclass
class InjectResult:
    app_name: str
    bundle_id: str
    executable: str
    injected: bool
    output_path: Path


def inject_bacon_core(source_ipa: Path, plugin_dylib: Path, output_ipa: Path) -> InjectResult:
    if not zipfile.is_zipfile(source_ipa):
        raise InjectError("這個檔案不是有效的 IPA/ZIP。")
    if not plugin_dylib.is_file():
        raise InjectError("伺服器找不到 BaconClickerCore.dylib。")

    with tempfile.TemporaryDirectory(prefix="baconipa_") as td_raw:
        td = Path(td_raw)
        with zipfile.ZipFile(source_ipa, "r") as zf:
            _safe_extract(zf, td)

        payload = td / "Payload"
        apps = [p for p in payload.glob("*.app") if p.is_dir()]
        if len(apps) != 1:
            raise InjectError(f"目前要求 Payload 內只有 1 個 .app；偵測到 {len(apps)} 個。")

        app = apps[0]
        info_path = app / "Info.plist"
        if not info_path.is_file():
            raise InjectError("找不到 app/Info.plist。")
        try:
            info = plistlib.loads(info_path.read_bytes())
        except Exception as e:
            raise InjectError(f"Info.plist 無法解析：{e}") from e

        executable_name = info.get("CFBundleExecutable")
        if not executable_name:
            raise InjectError("Info.plist 沒有 CFBundleExecutable。")
        executable = app / executable_name
        if not executable.is_file():
            raise InjectError(f"找不到主執行檔：{executable_name}")

        frameworks = app / "Frameworks"
        frameworks.mkdir(exist_ok=True)
        target_plugin = frameworks / "BaconClickerCore.dylib"
        shutil.copy2(plugin_dylib, target_plugin)
        patch_dylib_id(target_plugin, "@rpath/BaconClickerCore.dylib")

        load_path = "@executable_path/Frameworks/BaconClickerCore.dylib"
        injected = add_load_dylib(executable, load_path)

        # The IPA must be re-signed after Mach-O modification. Remove resource signature to avoid ambiguity.
        shutil.rmtree(app / "_CodeSignature", ignore_errors=True)

        # Small marker; harmless and useful for diagnostics.
        (app / "BaconInjection.txt").write_text(
            "BaconClickerCore injected with LC_LOAD_DYLIB.\n"
            "This IPA must be re-signed (for example with KSign) before installation.\n",
            encoding="utf-8",
        )

        output_ipa.parent.mkdir(parents=True, exist_ok=True)
        if output_ipa.exists():
            output_ipa.unlink()
        _zip_tree(td, output_ipa)

        display_name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or app.stem
        return InjectResult(
            app_name=str(display_name),
            bundle_id=str(info.get("CFBundleIdentifier") or ""),
            executable=str(executable_name),
            injected=injected,
            output_path=output_ipa,
        )
