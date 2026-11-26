"""Deterministic, content-addressed Website Studio build artifacts.

A release carries exactly one artifact: a reproducible tar of a finished Studio Next build
(source as built plus `.next`, without dependencies, caches or traces). The tar's sha256 is the
build checksum; a per-file manifest (and its sha256) lets a deployed copy be re-verified
file by file. No network, models or credentials. Fail closed on links and special files.
Revisit: when the Studio build layout or deployment format changes. Last touched: 2026-11-21.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path

KIND = 'next-start'  # a `next build` output served with `next start` from its exact lockfile
# Release builds for Vercel also carry the Build Output API tree (`vercel build`), whose functions
# reference each other through relative symlinks; only those links are allowed, and only inside it.
VERCEL_OUTPUT = ('.vercel', 'output')
KIND_VERCEL = 'next-start+vercel-prebuilt'
# Never part of a build artifact: dependencies (reinstalled from the recorded lockfile), runtime
# caches, build traces, the Studio adapter's intermediate source archive.
SKIP_TOP = frozenset({'node_modules', 'source.tar'})
SKIP_NEXT = frozenset({'cache', 'trace'})
# Written by `next start` while serving (e.g. a not-found page); never part of the build.
RUNTIME_LINKS = (('.next', 'cache'), ('.next', 'server', 'route-cache'))


class ArtifactError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _skipped(parts: tuple[str, ...]) -> bool:
    return ((parts[0] in SKIP_TOP) or (len(parts) >= 2 and parts[0] == '.next' and parts[1] in SKIP_NEXT)
            or parts[:3] == RUNTIME_LINKS[1])


def _check_link(folder: Path, parts: tuple[str, ...]) -> None:
    target = os.readlink(folder.joinpath(*parts))
    inside = folder.joinpath(*VERCEL_OUTPUT).resolve()
    resolved = (folder.joinpath(*parts[:-1]) / target).resolve()
    if parts[:2] != VERCEL_OUTPUT or os.path.isabs(target) or not resolved.is_relative_to(inside):
        raise ArtifactError(f'Unexpected link in build output: {"/".join(parts)}')


def files(folder: Path) -> list[tuple[str, Path]]:
    """Sorted (relative POSIX path, absolute path) for every artifact file; fails on links/specials."""
    folder = Path(folder)
    found = []
    for current, dirs, names in os.walk(folder, followlinks=False):
        base = Path(current)
        rel_base = base.relative_to(folder).parts
        keep = []
        for name in sorted(dirs):
            parts = rel_base + (name,)
            if _skipped(parts):
                continue
            if (base / name).is_symlink():
                if parts == ('node_modules',):
                    continue
                _check_link(folder, parts)
                found.append(('/'.join(parts), base / name))
                continue
            keep.append(name)
        dirs[:] = keep
        for name in sorted(names):
            parts = rel_base + (name,)
            if _skipped(parts):
                continue
            path = base / name
            mode = path.lstat().st_mode
            if parts[:1] == ('.vercel',) and parts[1:2] != ('output',) and name.startswith('.env'):
                raise ArtifactError(f'Environment file in build output: {"/".join(parts)}')
            if stat.S_ISLNK(mode):
                _check_link(folder, parts)
                found.append(('/'.join(parts), path))
                continue
            if not stat.S_ISREG(mode):
                raise ArtifactError(f'Unexpected link or special file in build output: {"/".join(parts)}')
            found.append(('/'.join(parts), path))
    found.sort()
    return found


def manifest(folder: Path) -> dict:
    rows = []
    for relative, path in files(folder):
        if path.is_symlink():
            target = os.readlink(path).encode()
            rows.append({'path': relative, 'link': target.decode(), 'sha256': hashlib.sha256(target).hexdigest(), 'size': len(target), 'mode': 0o777})
            continue
        mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
        rows.append({'path': relative, 'sha256': sha256_file(path), 'size': path.stat().st_size, 'mode': mode})
    kind = KIND_VERCEL if any(r['path'] == '.vercel/output/config.json' for r in rows) else KIND
    return {'kind': kind, 'files': rows, 'sha256': hashlib.sha256(canonical(rows)).hexdigest(),
            'count': len(rows), 'bytes': sum(r['size'] for r in rows)}


def package(folder: Path, destination: Path) -> dict:
    """Write a byte-reproducible tar of `folder` to `destination` (must not exist)."""
    folder, destination = Path(folder), Path(destination)
    listing = manifest(folder)
    if not any(r['path'] == '.next/BUILD_ID' for r in listing['files']):
        raise ArtifactError('Build output has no .next/BUILD_ID')
    with destination.open('xb') as raw, tarfile.open(fileobj=raw, mode='w', format=tarfile.GNU_FORMAT) as tar:
        for row in listing['files']:
            info = tarfile.TarInfo(row['path'])
            info.size, info.mode, info.mtime = row['size'], row['mode'], 0
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            if 'link' in row:
                info.type, info.linkname, info.size = tarfile.SYMTYPE, row['link'], 0
                tar.addfile(info)
                continue
            with (folder / row['path']).open('rb') as handle:
                tar.addfile(info, handle)
        meta = canonical({'kind': listing['kind'], 'manifest_sha256': listing['sha256']})
        info = tarfile.TarInfo('.ttros-artifact.json')
        info.size, info.mode, info.mtime = len(meta), 0o644, 0
        tar.addfile(info, io.BytesIO(meta))
    os.chmod(destination, 0o444)
    return {'kind': listing['kind'], 'sha256': sha256_file(destination), 'bytes': destination.stat().st_size,
            'manifest_sha256': listing['sha256'], 'files': listing['count'], 'content_bytes': listing['bytes'],
            'build_id': (folder / '.next/BUILD_ID').read_text().strip()}


def extract(artifact: Path, expected_sha256: str, expected_manifest: str, destination: Path) -> dict:
    """Extract only a verified artifact, then re-verify the extracted files against its manifest."""
    if sha256_file(artifact) != expected_sha256:
        raise ArtifactError('Artifact checksum does not match the release record')
    destination = Path(destination)
    destination.mkdir(parents=True)
    with tarfile.open(artifact, 'r') as tar:
        for member in tar.getmembers():
            linked = member.issym() and member.name.startswith('.vercel/output/') and not os.path.isabs(member.linkname)
            if not (member.isfile() or linked):
                raise ArtifactError(f'Artifact contains a non-file entry: {member.name}')
        tar.extractall(destination, filter='data')
    (destination / '.ttros-artifact.json').unlink()
    found = manifest(destination)
    if found['sha256'] != expected_manifest:
        raise ArtifactError('Extracted files do not match the artifact manifest')
    return found
