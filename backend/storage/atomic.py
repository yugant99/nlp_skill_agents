from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, TextIO


@contextmanager
def atomic_text_writer(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Iterator[TextIO]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        temporary_file = os.fdopen(
            descriptor,
            "w",
            encoding=encoding,
            newline=newline,
        )
        with temporary_file:
            yield temporary_file
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
        _sync_directory(destination.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    with atomic_text_writer(path, encoding=encoding) as output:
        output.write(content)


@contextmanager
def atomic_binary_writer(path: Path | str) -> Iterator[BinaryIO]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        binary_file = os.fdopen(descriptor, "wb")
        with binary_file:
            yield binary_file
            binary_file.flush()
            os.fsync(binary_file.fileno())
        os.replace(temporary_path, destination)
        _sync_directory(destination.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path | str, content: bytes) -> None:
    with atomic_binary_writer(path) as output:
        output.write(content)


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
