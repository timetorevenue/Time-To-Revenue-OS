"""Website Studio sources: registered local checkout, Google Drive, Business Brain and brand kit.

Deterministic only. Nothing here can reach a model: no David, model port or provider is
imported. Freshness is checked only when the operator asks (bounded API budget); there is no
timer, watcher or background AI. Reads are limited to paths and IDs registered in
context/website_studio_sites.json; nothing is written to a website checkout, Drive or the Brain.
Revisit: when source registration or the Drive/Brain readers change. Last touched: 2026-11-18.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import website_brand as brandkit
from website_studio_design import DEFAULTS

ROOT = Path(__file__).resolve().parents[2]
DRIVE_ID = re.compile(r'[A-Za-z0-9_-]{10,200}\Z')
DRIVE_URL = re.compile(r'/(?:folders|d)/([A-Za-z0-9_-]{10,200})')
FOLDER = 'application/vnd.google-apps.folder'
DRIVE_BUDGET = 24
REFERENCE_BUDGET_SECONDS = 90
EXCERPT = 1600
LISTING = 50


class SourceError(ValueError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _age_hours(stamp: str | None) -> float | None:
    if not stamp:
        return None
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)).total_seconds() / 3600


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


def default_drive():
    """The existing TTROS Drive reader (google.auth ADC, drive.readonly) with a bounded budget."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.drive_watcher import DriveHTTP
    return DriveHTTP(budget=DRIVE_BUDGET)


def default_brain(reference: str) -> dict:
    """Existing index-validated, scope-gated Shared Brain reader."""
    import shared_brain_read
    import aos_entity_index
    result = shared_brain_read.read(reference)
    if result.get('success'):
        meta = aos_entity_index.document_meta([reference]).get(reference)
        result['authority'] = shared_brain_read.authority_for(reference, meta)[0]
    return result


class Sources:
    def __init__(self, root: Path = ROOT, state: Path | None = None, drive_factory=default_drive,
                 brain_reader=default_brain, brain_root: Path | None = None):
        self.root = Path(root)
        self.state = Path(state or os.environ.get('TTROS_STUDIO_STATE') or self.root / 'results/website/studio')
        self.drive_factory = drive_factory
        self.brain_reader = brain_reader
        if brain_root is None:
            from business_brain import BUSINESS_BRAIN_ROOT
            brain_root = BUSINESS_BRAIN_ROOT
        self.brain_root = Path(brain_root)

    # ---- registry -------------------------------------------------------------------------
    def registry(self) -> dict:
        return json.loads((self.root / 'context/website_studio_sites.json').read_text(encoding='utf-8'))

    def site(self, site_id: str) -> dict:
        sites = self.registry()['sites']
        if not isinstance(site_id, str) or site_id not in sites:
            raise KeyError('Website is not registered for Studio')
        site = dict(sites[site_id], id=site_id)
        projects = json.loads((self.root / 'context/website_projects.json').read_text(encoding='utf-8'))['projects']
        project = projects.get(site['project'])
        if not project:
            raise KeyError('Studio site names an unregistered website project')
        site.update(name=project.get('name', site_id), repository=Path(project['repository']),
                    base_ref=project.get('base_ref', ''), client_scope=project.get('client_scope', ''))
        return site

    def sites(self) -> list[dict]:
        out = []
        for site_id in self.registry()['sites']:
            site = self.site(site_id)
            out.append({'id': site_id, 'label': site['label'], 'name': site['name'], 'default': bool(site.get('default')),
                        'page': site.get('page'), 'repository': str(site['repository']), 'base_ref': site['base_ref'],
                        'client_scope': site['client_scope']})
        return out

    def _dir(self, site_id: str) -> Path:
        return self.state / site_id

    def _repo_file(self, site: dict, relative: str) -> Path:
        repo = site['repository'].resolve()
        path = (repo / relative).resolve()
        if not path.is_relative_to(repo) or (repo / relative).is_symlink() or not path.is_file():
            raise SourceError(f'Registered file unavailable: {relative}')
        return path

    # ---- local checkout -------------------------------------------------------------------
    def _git(self, repo: Path, *args, timeout=20) -> str:
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                              timeout=timeout, check=True).stdout.strip()

    def revision(self, site: dict) -> dict:
        """The website revision the Studio adapter is pinned to, verified by file hashes."""
        studio = self.root / 'dashboard/frontend/src/studio'
        manifest = json.loads((studio / 'source-manifest.json').read_text())
        # Stage 3: the service-page adapter's files (ServicePage, services content, routes) too.
        services = json.loads((studio / 'service-manifest.json').read_text()) if (studio / 'service-manifest.json').is_file() else {'commit': manifest['commit'], 'files': {}}
        # Schema v5: the adapter is pinned to a COMMIT of the registered repository and verified from
        # its Git objects (what previews archive and build), so the checkout's HEAD is informational.
        repo = site['repository']
        problems = []
        head = self._git(repo, 'rev-parse', 'HEAD')
        if manifest['commit'] != services['commit']:
            problems.append('Studio homepage and service adapters are pinned to different website commits.')
        try:
            self._git(repo, 'cat-file', '-e', f"{manifest['commit']}^{{commit}}")
        except subprocess.CalledProcessError:
            problems.append(f"Pinned website commit {manifest['commit'][:12]} is not in the registered repository.")
        else:
            for relative, digest in {**manifest['files'], **services['files']}.items():
                blob = subprocess.run(['git', '-C', str(repo), 'show', f"{manifest['commit']}:{relative}"], capture_output=True, timeout=20)
                if blob.returncode or _sha(blob.stdout) != digest:
                    problems.append(f'{relative} at {manifest["commit"][:12]} does not match the adapter manifest.')
        return {'commit': manifest['commit'], 'head': head, 'head_matches': head == manifest['commit'], 'reads': 'committed objects',
                'files': len({**manifest['files'], **services['files']}), 'current': not problems, 'problems': problems}

    def local_status(self, site: dict) -> dict:
        repo = site['repository']
        base = {'kind': 'local', 'label': 'Local website', 'repository': str(repo), 'base_ref': site['base_ref'],
                'checked_at': now(), 'writes': 'Never written by Studio'}
        if not repo.is_dir():
            return {**base, 'state': 'disconnected', 'detail': 'Registered checkout directory is missing.'}
        try:
            branch = self._git(repo, 'branch', '--show-current')
            dirty = [line for line in self._git(repo, 'status', '--porcelain').splitlines() if line]
            origin = self._git(repo, 'remote', 'get-url', 'origin')
            revision = self.revision(site)
        except (subprocess.SubprocessError, OSError) as exc:
            return {**base, 'state': 'error', 'detail': f'Git read failed: {type(exc).__name__}'}
        stale = not revision['current'] or bool(dirty)
        detail = (('Clean; pinned Studio adapter revision verified.' + ('' if revision.get('head_matches', True) else f" Studio reads committed {revision['commit'][:7]}; checkout HEAD {revision['head'][:7]} is not used.")) if not stale else
                  '; '.join(revision['problems'] + ([f'{len(dirty)} uncommitted change(s).'] if dirty else [])))
        return {**base, 'state': 'stale' if stale else 'connected', 'branch': branch, 'head': revision['head'],
                'origin': origin, 'dirty': len(dirty), 'revision': revision, 'detail': detail,
                'last_success': base['checked_at'] if not stale else None}

    # ---- reference checkout (Windows) -----------------------------------------------------
    def check_reference(self, site: dict) -> list[dict]:
        out = []
        for ref in site.get('reference_checkouts', []):
            path = Path(ref['path'])
            row = {'id': ref['id'], 'label': ref['label'], 'path': ref['path'], 'role': ref['role'], 'note': ref['note'],
                   'checked_at': now(), 'writes': 'Never written by Studio'}
            if not path.is_dir():
                out.append({**row, 'state': 'disconnected', 'detail': 'Folder is not reachable from WSL.'})
                continue
            provenance = json.loads((self.root / ref['provenance']).read_text())
            started, same, changed, skipped = time.monotonic(), 0, [], 0
            for item in provenance['source_file_hashes']:
                if time.monotonic() - started > REFERENCE_BUDGET_SECONDS:
                    skipped += 1
                    continue
                candidate = path / item['path']
                if candidate.is_file() and _sha(candidate.read_bytes()) == item['sha256']:
                    same += 1
                else:
                    changed.append(item['path'])
            try:
                head = self._git(path, 'rev-parse', 'HEAD', timeout=60)
                status = [line for line in self._git(path, 'status', '--porcelain', timeout=120).splitlines() if line]
            except (subprocess.SubprocessError, OSError) as exc:
                head, status = None, None
                row['git_error'] = type(exc).__name__
            known = {item['path'] for item in provenance['source_file_hashes']}
            outside = [line[3:].strip().strip('"') for line in status or [] if line[3:].strip().strip('"') not in known]
            state = 'connected' if not changed and not skipped else 'stale'
            out.append({**row, 'state': state, 'head': head, 'snapshot_head': provenance['source_head'],
                        'snapshot_commit': provenance.get('snapshot_commit'), 'files_checked': same + len(changed),
                        'files_matching': same, 'changed_since_snapshot': changed[:20], 'skipped': skipped,
                        'not_in_snapshot': outside[:40], 'not_in_snapshot_count': len(outside),
                        'last_success': row['checked_at'] if state == 'connected' else None,
                        'detail': (f'{same}/{len(known)} snapshotted files identical to the WSL checkout. '
                                   f'{len(outside)} Windows-only change(s) were never snapshotted and are not used.'
                                   if state == 'connected' else
                                   f'{len(changed)} snapshotted file(s) changed on Windows since the snapshot; re-snapshot explicitly before using them.')})
        return out

    # ---- Google Drive ---------------------------------------------------------------------
    def _drive_selection(self, site_id: str):
        return _read(self._dir(site_id) / 'drive-selection.json')

    def _list(self, api, folder_id: str) -> list[dict]:
        result = api.get('files', q=f"'{folder_id}' in parents and trashed=false",
                         fields='files(id,name,mimeType,modifiedTime,size,webViewLink)', pageSize=LISTING,
                         orderBy='modifiedTime desc', supportsAllDrives='true', includeItemsFromAllDrives='true')
        return [{k: f.get(k) for k in ('id', 'name', 'mimeType', 'modifiedTime', 'size', 'webViewLink')} for f in result.get('files', [])]

    def refresh_drive(self, site: dict) -> dict:
        state_dir = self._dir(site['id'])
        previous = _read(state_dir / 'drive.json') or {}
        record = {'checked_at': now(), 'last_success': previous.get('last_success'), 'files': {}, 'folders': {}}
        try:
            api = self.drive_factory()
        except Exception as exc:  # credential discovery: report the exact missing capability
            record.update(state='disconnected', error=f'{type(exc).__name__}: Google Application Default Credentials with the drive.readonly scope are unavailable to the TTROS backend.')
            _atomic(state_dir / 'drive.json', {**previous, **record})
            return record
        try:
            drive = site['drive']
            for item in [drive['brand_kit'], *drive.get('brand_assets', [])]:
                meta = api.metadata(item['id'])
                record['files'][item['id']] = {'label': item['label'], **{k: meta.get(k) for k in ('id', 'name', 'mimeType', 'modifiedTime', 'version', 'size', 'webViewLink', 'parents')}}
            folders = list(drive.get('folders', []))
            selected = self._drive_selection(site['id'])
            if selected:
                folders.append({'id': selected['id'], 'role': 'website_assets', 'label': selected['name']})
            for folder in folders:
                meta = api.metadata(folder['id'])
                if meta.get('mimeType') != FOLDER:
                    raise SourceError(f"Registered folder {folder['id']} is no longer a folder")
                record['folders'][folder['id']] = {'role': folder['role'], 'label': folder['label'], 'name': meta.get('name'),
                                                   'modifiedTime': meta.get('modifiedTime'), 'children': self._list(api, folder['id'])}
            kit = record['files'][drive['brand_kit']['id']]
            cached = previous.get('brand_kit') or {}
            pdf = state_dir / 'brand-kit.pdf'
            if cached.get('version') != kit.get('version') or cached.get('modifiedTime') != kit.get('modifiedTime') or not pdf.is_file():
                raw = api.get('files/' + drive['brand_kit']['id'], alt='media', supportsAllDrives='true')
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(raw)
                kit['downloaded_at'] = record['checked_at']
            else:
                kit['downloaded_at'] = cached.get('downloaded_at')
            kit['sha256'] = _sha(pdf.read_bytes())
            record['brand_kit'] = kit
            record.update(state='connected', last_success=record['checked_at'], error=None,
                          api_calls=getattr(api, 'stats', {}).get('calls'))
        except Exception as exc:
            record.update(state='error', error=f'{type(exc).__name__}: {str(exc)[:200]}',
                          brand_kit=previous.get('brand_kit'), files=previous.get('files', {}), folders=previous.get('folders', {}))
        _atomic(state_dir / 'drive.json', record)
        return record

    def drive_status(self, site: dict) -> dict:
        record = _read(self._dir(site['id']) / 'drive.json')
        excluded = site['drive'].get('excluded_folders', [])
        selection = self._drive_selection(site['id'])
        base = {'kind': 'drive', 'label': 'Google Drive', 'connector': 'Existing TTROS Drive reader (google.auth ADC, drive.readonly)',
                'excluded_folders': excluded, 'asset_folder': selection, 'writes': 'Read-only'}
        if not record:
            return {**base, 'state': 'stale', 'detail': 'Never checked. Use Check sources to read registered Drive IDs.', 'last_success': None}
        age = _age_hours(record.get('last_success'))
        state = record.get('state', 'error')
        if state == 'connected' and (age is None or age > site.get('freshness_hours', 24)):
            state = 'stale'
        detail = record.get('error') or ('Registered files and folders verified.' if state == 'connected' else
                                         f"Last verified {record.get('last_success')}; older than {site.get('freshness_hours', 24)}h.")
        if not selection:
            detail += ' No website asset folder selected; the project-context folder is not assumed to be the asset folder.'
        return {**base, 'state': state, 'detail': detail, 'checked_at': record.get('checked_at'),
                'last_success': record.get('last_success'), 'files': record.get('files', {}), 'folders': record.get('folders', {}),
                'brand_kit': record.get('brand_kit')}

    def select_drive_folder(self, site_id: str, value: str | None) -> dict:
        site = self.site(site_id)
        target = self._dir(site_id) / 'drive-selection.json'
        if value in (None, ''):
            target.unlink(missing_ok=True)
            return {'asset_folder': None}
        value = value.strip()
        match = DRIVE_URL.search(value)
        ident = match.group(1) if match else value
        if not DRIVE_ID.fullmatch(ident):
            raise SourceError('Enter a Google Drive folder link or folder ID.')
        if ident in {f['id'] for f in site['drive'].get('excluded_folders', [])}:
            raise SourceError('That folder is excluded: it holds unrelated client material.')
        try:
            api = self.drive_factory()
            meta = api.metadata(ident)
        except Exception as exc:
            raise SourceError(f'Drive could not verify that ID ({type(exc).__name__}). Nothing was saved.') from exc
        if meta.get('mimeType') != FOLDER or meta.get('trashed'):
            raise SourceError('That ID is not an active Google Drive folder.')
        children = self._list(api, ident)
        selection = {'id': ident, 'name': meta.get('name'), 'verified_at': now(), 'modifiedTime': meta.get('modifiedTime'),
                     'images': sum(1 for c in children if str(c.get('mimeType', '')).startswith('image/')),
                     'documents': sum(1 for c in children if not str(c.get('mimeType', '')).startswith('image/') and c.get('mimeType') != FOLDER),
                     'listed': len(children)}
        _atomic(target, selection)
        return {'asset_folder': selection}

    # ---- Business Brain -------------------------------------------------------------------
    def brain_records(self, site: dict) -> list[dict]:
        rows = []
        for reference in site.get('brain', []):
            try:
                result = self.brain_reader(reference)
            except Exception as exc:
                rows.append({'reference': reference, 'state': 'error', 'detail': type(exc).__name__})
                continue
            relative = reference.removeprefix('business_brain:')
            path = self.brain_root / relative
            if result.get('success'):
                content = result.get('content', '')
                title = next((line.lstrip('# ').strip() for line in content.splitlines() if line.startswith('# ')), relative)
                rows.append({'reference': reference, 'state': 'connected', 'title': title, 'authority': result.get('authority'),
                             'modified': dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(timespec='seconds') if path.is_file() else None,
                             'sha256': _sha(content.encode()), 'excerpt': content[:EXCERPT], 'truncated': len(content) > EXCERPT or bool(result.get('next_offset'))})
            else:
                state = 'stale' if path.is_file() else 'disconnected'
                rows.append({'reference': reference, 'state': state,
                             'detail': 'Record changed since indexing; reindex before use.' if state == 'stale' else result.get('error', 'unavailable')})
        return rows

    def brain_status(self, site: dict) -> dict:
        checked = now()
        if not self.brain_root.is_dir():
            return {'kind': 'brain', 'label': 'Business Brain', 'state': 'disconnected', 'checked_at': checked,
                    'detail': 'Business Brain vault is not reachable.', 'records': [], 'last_success': None}
        records = self.brain_records(site)
        states = {r['state'] for r in records}
        state = 'error' if 'error' in states else 'disconnected' if states == {'disconnected'} else 'stale' if states - {'connected'} else 'connected'
        return {'kind': 'brain', 'label': 'Business Brain', 'state': state, 'checked_at': checked,
                'reader': 'Existing index-validated Shared Brain reader (global scope)', 'records': records,
                'last_success': checked if state == 'connected' else None, 'writes': 'Read-only',
                'detail': f"{sum(r['state'] == 'connected' for r in records)}/{len(records)} registered records readable."}

    # ---- site guidance (repository files) -------------------------------------------------
    def guidance(self, site: dict) -> list[dict]:
        out = []
        for relative in site.get('site_guidance', []):
            try:
                path = self._repo_file(site, relative)
            except SourceError as exc:
                out.append({'path': relative, 'state': 'disconnected', 'detail': str(exc)})
                continue
            raw = path.read_bytes()
            out.append({'path': relative, 'state': 'connected', 'sha256': _sha(raw), 'excerpt': raw.decode('utf-8', 'replace')[:EXCERPT]})
        return out

    # ---- brand ----------------------------------------------------------------------------
    def build_brand(self, site: dict) -> dict:
        """Deterministic proposal from the brand kit. Never calls a model; unreadable parts are reported."""
        state_dir = self._dir(site['id'])
        drive = _read(state_dir / 'drive.json') or {}
        pdf = state_dir / 'brand-kit.pdf'
        local_rel = site['drive']['brand_kit'].get('local_copy')
        local = None
        try:
            local_path = self._repo_file(site, local_rel) if local_rel else None
            local = {'path': local_rel, 'sha256': _sha(local_path.read_bytes())} if local_path else None
        except SourceError:
            local = {'path': local_rel, 'sha256': None}
        if pdf.is_file():
            raw, origin = pdf.read_bytes(), 'google_drive'
        elif local and local['sha256']:
            raw, origin = self._repo_file(site, local_rel).read_bytes(), 'local_copy'
        else:
            raise SourceError('Brand kit unavailable: check Drive sources first.')
        text, pages = brandkit.pdf_text(raw)
        brand = brandkit.extract(text)
        homepage = json.loads((self.root / 'dashboard/frontend/src/studio/homepage.json').read_text())
        site_css = brandkit.site_styles(site['repository'])
        tokens, exceptions = brandkit.proposed_tokens(brand, DEFAULTS)
        kit = drive.get('brand_kit') or {}
        source = {'origin': origin, 'drive_id': site['drive']['brand_kit']['id'], 'name': kit.get('name'),
                  'modifiedTime': kit.get('modifiedTime'), 'version': kit.get('version'), 'webViewLink': kit.get('webViewLink'),
                  'sha256': _sha(raw), 'pages': pages, 'local_copy': local,
                  'local_copy_matches': bool(local and local.get('sha256') == _sha(raw))}
        proposal = {'site': site['id'], 'created_at': now(), 'source': source, 'brand': brand,
                    'extraction': {'method': brandkit.EXTRACTOR, 'model_calls': 0, 'unextracted': brand['unextracted'],
                                   'interpretation_needed': brand['needs_interpretation']},
                    'site_styles': site_css, 'conflicts': brandkit.conflicts(brand, site_css, homepage, self.guidance(site)),
                    'tokens': tokens, 'token_changes': {k: {'site': DEFAULTS[k], 'brand': v} for k, v in tokens.items() if v != DEFAULTS[k]},
                    'exceptions': exceptions, 'rules': brandkit.rules(brand, exceptions),
                    'precedence': 'Visual identity: brand kit. Copy and positioning: the 14 Nov 2026 addendum and Business Brain, which supersede brand-kit messaging.',
                    'logos': self.logos(site)}
        proposal['fingerprint'] = brandkit.fingerprint({k: proposal[k] for k in ('source', 'brand', 'tokens', 'exceptions', 'rules')})
        _atomic(state_dir / 'brand-proposed.json', proposal)
        return proposal

    def logos(self, site: dict) -> list[dict]:
        out = []
        drive = (_read(self._dir(site['id']) / 'drive.json') or {}).get('files', {})
        for item in site.get('local_brand_assets', []):
            try:
                path = self._repo_file(site, item['path'])
                out.append({'id': item['id'], 'label': item['label'], 'path': item['path'], 'source': 'local website', 'bytes': path.stat().st_size,
                            'url': f"/api/website/studio/sites/{site['id']}/assets/{item['id']}"})
            except SourceError:
                out.append({'id': item['id'], 'label': item['label'], 'path': item['path'], 'source': 'local website', 'missing': True})
        for item in site['drive'].get('brand_assets', []):
            meta = drive.get(item['id'], {})
            out.append({'id': item['id'], 'label': item['label'], 'source': 'google drive', 'name': meta.get('name'),
                        'webViewLink': meta.get('webViewLink'), 'modifiedTime': meta.get('modifiedTime'), 'verified': bool(meta)})
        return out

    def asset(self, site_id: str, asset_id: str) -> Path:
        site = self.site(site_id)
        item = next((a for a in site.get('local_brand_assets', []) if a['id'] == asset_id), None)
        if not item:
            raise KeyError('Unregistered asset')
        return self._repo_file(site, item['path'])

    def brand(self, site_id: str) -> dict:
        state_dir = self._dir(site_id)
        return {'proposed': _read(state_dir / 'brand-proposed.json'), 'approved': _read(state_dir / 'brand-approved.json')}

    def approved_rules(self, site_id: str) -> dict | None:
        approved = _read(self._dir(site_id) / 'brand-approved.json')
        return approved['rules'] if approved else None

    def approve_brand(self, site_id: str, fingerprint: str, actor: str) -> dict:
        self.site(site_id)
        proposed = _read(self._dir(site_id) / 'brand-proposed.json')
        if not proposed:
            raise SourceError('No brand proposal to approve.')
        if proposed['fingerprint'] != fingerprint:
            raise SourceError('The brand proposal changed; review the current proposal before approving.')
        approved = {'approved_at': now(), 'approved_by': actor, 'fingerprint': fingerprint, 'source': proposed['source'],
                    'rules': proposed['rules'], 'tokens': proposed['tokens'], 'exceptions': proposed['exceptions'],
                    'messaging': proposed['brand'].get('messaging'), 'voice': proposed['brand'].get('voice'),
                    'precedence': proposed['precedence']}
        _atomic(self._dir(site_id) / 'brand-approved.json', approved)
        return approved

    def withdraw_brand(self, site_id: str) -> None:
        self.site(site_id)
        (self._dir(site_id) / 'brand-approved.json').unlink(missing_ok=True)

    def brand_status(self, site: dict) -> dict:
        brand = self.brand(site['id'])
        proposed, approved = brand['proposed'], brand['approved']
        drive = _read(self._dir(site['id']) / 'drive.json') or {}
        kit = drive.get('brand_kit') or {}
        base = {'kind': 'brand', 'label': 'Brand Guide', 'drive_id': site['drive']['brand_kit']['id']}
        if not proposed:
            return {**base, 'state': 'disconnected', 'approval': 'none', 'detail': 'Brand kit not read yet. Check sources.', 'last_success': None}
        state = 'connected'
        detail = f"{proposed['source'].get('name') or 'Brand kit'} read deterministically ({proposed['source']['pages']} pages, 0 model calls)."
        if kit.get('sha256') and kit['sha256'] != proposed['source']['sha256']:
            state, detail = 'stale', 'The brand kit changed in Drive since this proposal; rebuild it.'
        if approved and approved['source']['sha256'] != proposed['source']['sha256']:
            state, detail = 'stale', 'The approved brand configuration came from an older brand kit; review the new proposal.'
        return {**base, 'state': state, 'detail': detail, 'approval': 'approved' if approved else 'proposed',
                'approved_at': approved and approved['approved_at'], 'last_success': proposed['created_at'],
                'source': proposed['source'], 'conflicts': sum(c['severity'] in ('conflict', 'superseded') for c in proposed['conflicts'])}

    # ---- aggregate ------------------------------------------------------------------------
    def status(self, site_id: str) -> dict:
        site = self.site(site_id)
        state_dir = self._dir(site_id)
        reference = _read(state_dir / 'reference.json') or [
            {'id': r['id'], 'label': r['label'], 'path': r['path'], 'role': r['role'], 'note': r['note'], 'state': 'stale',
             'detail': 'Not compared yet. Check sources compares registered file hashes (read-only).', 'last_success': None}
            for r in site.get('reference_checkouts', [])]
        return {'site': {k: (str(v) if isinstance(v, Path) else v) for k, v in site.items() if k in ('id', 'label', 'name', 'repository', 'base_ref', 'client_scope', 'page')},
                'model_calls': 0,
                'local': self.local_status(site), 'reference_checkouts': reference, 'drive': self.drive_status(site),
                'brain': self.brain_status(site), 'brand': self.brand_status(site), 'guidance': self.guidance(site),
                'last_refresh': (_read(state_dir / 'refresh.json') or {}).get('finished_at')}

    def refresh(self, site_id: str, reference: bool = True) -> dict:
        """Operator-requested bounded freshness check. Deterministic; no model; no writes outside state."""
        site = self.site(site_id)
        started = now()
        drive = self.refresh_drive(site)
        brand_error = None
        try:
            self.build_brand(site)
        except Exception as exc:  # brand failure is reported; never handed to a model
            brand_error = f'{type(exc).__name__}: {str(exc)[:200]}'
        if reference:
            _atomic(self._dir(site_id) / 'reference.json', self.check_reference(site))
        _atomic(self._dir(site_id) / 'refresh.json', {'started_at': started, 'finished_at': now(), 'drive_state': drive.get('state'),
                                                       'brand_error': brand_error, 'model_calls': 0})
        result = self.status(site_id)
        result['brand_error'] = brand_error
        return result

    # ---- David context pack (read-only, bounded) ------------------------------------------
    def context(self, site_id: str) -> dict:
        site = self.site(site_id)
        brand = self.brand(site_id)
        chosen = brand['approved'] or brand['proposed']
        pack = {'brand_status': 'approved' if brand['approved'] else 'proposed_not_authoritative' if brand['proposed'] else 'unavailable'}
        if chosen:
            proposal = brand['proposed'] or {}
            pack['brand'] = {'rules': chosen['rules'], 'tokens': chosen['tokens'], 'precedence': chosen.get('precedence') or proposal.get('precedence'),
                             'voice': (chosen.get('voice') or proposal.get('brand', {}).get('voice')),
                             'messaging_from_kit': chosen.get('messaging') or proposal.get('brand', {}).get('messaging'),
                             'source': chosen['source'].get('drive_id')}
        pack['brain'] = [{'reference': r['reference'], 'authority': r.get('authority'), 'excerpt': r.get('excerpt', '')[:1200]}
                         for r in self.brain_records(site) if r['state'] == 'connected']
        pack['guidance'] = [{'path': g['path'], 'sha256': g['sha256'][:12], 'excerpt': g['excerpt'][:1200]} for g in self.guidance(site) if g['state'] == 'connected']
        pack['sources'] = ([f"drive:{pack['brand']['source']}"] if chosen else []) + [r['reference'] for r in pack['brain']] + [f"{site['project']}:{g['path']}" for g in pack['guidance']]
        return pack
