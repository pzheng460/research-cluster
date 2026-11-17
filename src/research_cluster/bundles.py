import io
from pathlib import Path, PurePosixPath
import stat
import zipfile


MAX_BUNDLE = 20 * 1024 * 1024


def pack_files(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def unpack_files(data, destination):
    if len(data) > MAX_BUNDLE:
        raise ValueError("Archive exceeds 20 MiB")
    paths, total = [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000:
            raise ValueError("Archive contains too many files")
        for item in entries:
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or not item.filename or stat.S_ISLNK(mode)):
                raise ValueError("Unsafe archive path")
            total += item.file_size
            if total > MAX_BUNDLE:
                raise ValueError("Expanded archive exceeds 20 MiB")
            target = Path(destination).joinpath(*path.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
            paths.append({"name": path.as_posix(), "size": item.file_size})
    return paths
