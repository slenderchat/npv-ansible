#!/usr/bin/python
# -*- coding: utf-8 -*-
# Read-only planning step of the apt_sources role. It recognizes only simple
# entries for the official Debian archives; other repositories are left alone
# and anything it cannot classify safely is reported as a blocker.

from __future__ import annotations

import os
import re
import stat

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: apt_sources_inspect
short_description: Plan the apt_sources change without modifying the host
description:
  - Classifies the active APT source files and preferences and reports
    migration blockers.
  - When a component or backports eligibility would be removed, checks the
    installed packages against the local APT cache.
  - Never writes files and never contacts mirrors.
options:
  components: {type: list, elements: str, required: true}
  backports_packages: {type: list, elements: str, required: true}
  sources_path: {type: path, required: true}
  sources_content: {type: str, required: true}
  preferences_path: {type: path, required: true}
  marker: {type: str, required: true}
  refresh_flag_path: {type: path, required: true}
"""

SOURCES_LIST = '/etc/apt/sources.list'
SOURCES_DIR = '/etc/apt/sources.list.d'
PREFERENCES = '/etc/apt/preferences'
PREFERENCES_DIR = '/etc/apt/preferences.d'

# The file names APT reads (sources.list(5), apt_preferences(5)).
SOURCE_NAME = re.compile(r'^[A-Za-z0-9_.-]+\.(list|sources)$')
PREFERENCE_NAME = re.compile(r'^([A-Za-z0-9_-]+|[A-Za-z0-9_.-]+\.pref)$')

ARCHIVES = {
    'debian': re.compile(
        r'^https?://(deb|ftp|ftp\.[a-z]{2})\.debian\.org/debian/?$'),
    'security': re.compile(
        r'^https?://(security|deb)\.debian\.org/debian-security/?$'),
}
ARCHIVE_URI = {
    'debian': 'https://deb.debian.org/debian',
    'security': 'https://security.debian.org/debian-security',
}
SUITE_ARCHIVE = {
    'trixie': 'debian',
    'trixie-updates': 'debian',
    'trixie-security': 'security',
    'trixie-backports': 'debian',
}
BACKPORTS = 'trixie-backports'
COMPONENTS = ('main', 'contrib', 'non-free', 'non-free-firmware')
KEYRINGS = (
    '/usr/share/keyrings/debian-archive-keyring.pgp',
    '/usr/share/keyrings/debian-archive-keyring.gpg',
)
DISABLED = ('no', 'false', 'off', '0')

ONE_LINE = re.compile(
    r'^(deb|deb-src)(?:\s+\[([^\]]*)\])?\s+(\S+)\s+(\S+)((?:\s+\S+)*)$')
# A preference value that APT would read as a glob or a regular expression.
PATTERN_VALUE = re.compile(r'[*?\[]|^/.*/$')
# "Pin: release" fields whose trixie-backports value does not move between
# releases; any other literal value in one of them cannot match backports.
# Suite aliases (a=) move between releases and are never taken as proof.
BACKPORTS_RELEASE = {
    'n': (BACKPORTS,),
    'o': ('debian backports',),
    'l': ('debian backports',),
}
# "apt-cache policy" output, read with LC_ALL=C.
POLICY_FILE = re.compile(r'^\s*-?\d+ (\S+) ([^/\s]+)/(\S+) \S+ Packages$')
POLICY_VERSION = re.compile(r'^ (?:\*\*\*| {3}) (\S+) (-?\d+)$')
POLICY_SOURCE = re.compile(
    r'^\s+(-?\d+) (\S+)(?: ([^/\s]+)/(\S+) \S+ Packages)?$')
# A version below this priority does not replace an installed version, whose
# dpkg status record alone has priority 100.
UPDATE_PRIORITY = 100


def read_text(path):
    with open(path, encoding='utf-8', errors='replace') as handle:
        return handle.read()


def apt_files(directory, pattern):
    if not os.path.isdir(directory):
        return []
    return sorted(os.path.join(directory, name)
                  for name in os.listdir(directory) if pattern.match(name))


def is_regular(path):
    return os.path.isfile(path) and not os.path.islink(path)


def archive_of(uri):
    for archive, pattern in ARCHIVES.items():
        if pattern.match(uri):
            return archive
    return None


def describe(source):
    archive, suite, component = source
    return '%s %s/%s' % (ARCHIVE_URI[archive], suite, component)


def keep_hint(source, name=None):
    if source[1] == BACKPORTS:
        return 'add %s to apt_sources_backports_packages' % (
            name or 'the packages you need')
    return "keep '%s' in apt_sources_components" % source[2]


def parse_one_line(text):
    entries, problems = [], []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = ONE_LINE.match(line)
        if not match:
            problems.append('line %d is not a recognizable deb or deb-src '
                            'entry' % number)
            continue
        options = {}
        for option in (match.group(2) or '').split():
            key, _, value = option.partition('=')
            options[key.lower()] = value
        entries.append({
            'where': 'line %d' % number,
            'types': [match.group(1)],
            'uris': [match.group(3)],
            'suites': [match.group(4)],
            'components': match.group(5).split(),
            'options': options,
        })
    return entries, problems, []


def deb822_stanzas(text):
    """Return [(first line number, {lowercase field: value})] or raise."""
    stanzas, fields, field, start = [], {}, None, 0
    for number, raw in enumerate(text.splitlines() + [''], 1):
        if raw.startswith('#'):
            continue
        if not raw.strip():
            if fields:
                stanzas.append((start, fields))
            fields, field = {}, None
            continue
        if raw[0] in ' \t':
            if field is None:
                raise ValueError('line %d is a continuation without a field'
                                 % number)
            fields[field] += '\n' + raw.strip()
            continue
        key, sep, value = raw.partition(':')
        if not sep or not key.strip():
            raise ValueError('line %d is not a deb822 field' % number)
        if not fields:
            start = number
        field = key.strip().lower()
        fields[field] = value.strip()
    return stanzas


def parse_deb822(text):
    """Return active entries, problems and disabled stanzas."""
    entries, problems, disabled = [], [], []
    try:
        stanzas = deb822_stanzas(text)
    except ValueError as error:
        return [], [str(error)], []
    for start, fields in stanzas:
        where = 'stanza at line %d' % start
        enabled = fields.get('enabled', 'yes').lower() not in DISABLED
        missing = [name for name in ('types', 'uris', 'suites')
                   if not fields.get(name)]
        if missing:
            if enabled:
                problems.append('the stanza at line %d lacks %s'
                                % (start, ', '.join(missing)))
            else:
                disabled.append((where, None))
            continue
        entry = {
            'where': where,
            'types': fields['types'].split(),
            'uris': fields['uris'].split(),
            'suites': fields['suites'].split(),
            'components': fields.get('components', '').split(),
            'options': {key: value for key, value in fields.items()
                        if key not in ('types', 'uris', 'suites',
                                       'components', 'enabled')},
        }
        if enabled:
            entries.append(entry)
        else:
            disabled.append((where, entry))
    return entries, problems, disabled


def is_official(entry):
    return entry is not None and all(
        archive_of(uri) and not official_problem(entry, archive_of(uri))
        for uri in entry['uris'])


def official_problem(entry, archive):
    """Why an entry for an official archive cannot be migrated, or None."""
    if set(entry['types']) - {'deb', 'deb-src'}:
        return 'has types other than deb and deb-src'
    for suite in entry['suites']:
        if SUITE_ARCHIVE.get(suite) != archive:
            return 'uses suite %s, which this role does not migrate' % suite
    if not entry['components'] or set(entry['components']) - set(COMPONENTS):
        return 'has components outside %s' % ', '.join(COMPONENTS)
    extra = sorted(set(entry['options']) - {'signed-by'})
    if extra:
        return 'has options this role does not keep: %s' % ', '.join(extra)
    if entry['options'].get('signed-by', KEYRINGS[0]) not in KEYRINGS:
        return 'is not signed by the Debian archive keyring'
    return None


def inspect_sources(params, blockers):
    """Return the official sources in use and the files to migrate."""
    managed = params['sources_path']
    current, remove, stub, report = set(), [], False, []
    for path in [SOURCES_LIST] + apt_files(SOURCES_DIR, SOURCE_NAME):
        if path == managed and os.path.lexists(path) and not is_regular(path):
            blockers.append('%s: not a regular file; it is not replaced'
                            % path)
            continue
        if not os.path.isfile(path):
            continue
        try:
            text = read_text(path)
        except OSError as error:
            blockers.append('%s: cannot be read: %s' % (path, error))
            continue
        parse = parse_deb822 if path.endswith('.sources') else parse_one_line
        entries, problems, disabled = parse(text)
        official, others = [], []
        foreign_disabled = [where for where, entry in disabled
                            if not is_official(entry)]
        for entry in entries:
            for uri in entry['uris']:
                archive = archive_of(uri)
                if archive is None:
                    others.append(uri)
                    if any(suite.startswith(('trixie-backports',
                                             'stable-backports'))
                           for suite in entry['suites']):
                        problems.append(
                            '%s: backports from %s is outside the policy of '
                            'this role; remove it' % (entry['where'], uri))
                    elif (entry['options'].get('signed-by') in KEYRINGS
                          and set(entry['suites']) & set(SUITE_ARCHIVE)):
                        # Signed by the Debian archive keyring for a managed
                        # suite: a Debian mirror, not a vendor repository.
                        problems.append(
                            '%s: %s is signed by the Debian archive keyring '
                            'for a managed suite, so it looks like a Debian '
                            'mirror that this role does not migrate; remove '
                            'or disable it manually' % (entry['where'], uri))
                    continue
                reason = official_problem(entry, archive)
                if reason:
                    problems.append('%s: %s %s' % (entry['where'], uri, reason))
                else:
                    official.append((archive, entry))
        if problems:
            blockers.extend('%s: %s' % (path, problem) for problem in problems)
            continue
        if others and (official or path == managed):
            blockers.append(
                '%s: %s other repositories (%s); move those to a separate '
                'file first' % (
                    path, 'mixes the official Debian archives with' if official
                    else 'would be replaced but holds',
                    ', '.join(sorted(set(others)))))
            continue
        if foreign_disabled and (official or path == managed):
            blockers.append(
                '%s: the disabled %s would be lost; move it to a separate '
                'file first' % (path, ', '.join(foreign_disabled)))
            continue
        for archive, entry in official:
            if 'deb' in entry['types']:
                current.update((archive, suite, component)
                               for suite in entry['suites']
                               for component in entry['components'])
        if not official or path == managed:
            continue
        if not is_regular(path):
            blockers.append('%s: holds official Debian entries but is not a '
                            'regular file; migrate it manually' % path)
            continue
        if path == SOURCES_LIST:
            stub = True
            report.append('%s: %d official Debian entries move to %s; the '
                          'file keeps only a comment'
                          % (path, len(official), managed))
        else:
            remove.append(path)
            report.append('%s: %d official Debian entries move to %s; the '
                          'file is removed' % (path, len(official), managed))
    return current, remove, stub, report


def pref_stanzas(text):
    """Return [(first line number, {lowercase field: value})]."""
    stanzas, fields, start = [], {}, 0
    for number, raw in enumerate(text.splitlines() + [''], 1):
        if raw.startswith('#'):
            continue
        if not raw.strip():
            if fields:
                stanzas.append((start, fields))
            fields = {}
            continue
        key, sep, value = raw.partition(':')
        if sep:
            if not fields:
                start = number
            fields[key.strip().lower()] = value.strip()
    return stanzas


def backports_overlap(fields):
    """None if the record provably cannot match trixie-backports."""
    kind, _, spec = fields.get('pin', '').partition(' ')
    kind, spec = kind.lower(), spec.strip()
    if kind == 'origin':
        host = spec.strip('"')
        if not PATTERN_VALUE.search(host) and host.lower() != 'deb.debian.org':
            return None
    elif kind == 'release':
        for term in spec.split(','):
            key, sep, value = term.partition('=')
            key, value = key.strip().lower(), value.strip().strip('"')
            if (sep and key in BACKPORTS_RELEASE and value
                    and not PATTERN_VALUE.search(value)
                    and value.lower() not in BACKPORTS_RELEASE[key]):
                return None
    elif not kind:
        return 'has no Pin field'
    return 'Pin: %s may match %s' % (fields['pin'], BACKPORTS)


def managed_allowlist(text):
    """The allowlist of the role's own file, or None if not restrictive."""
    allowed, restricted = set(), False
    for _, fields in pref_stanzas(text):
        if fields.get('pin') != 'release n=' + BACKPORTS:
            continue
        if fields.get('package') == '*' and fields.get('pin-priority') == '-1':
            restricted = True
        elif fields.get('pin-priority') == '100':
            allowed = set(fields.get('package', '').split())
    return allowed if restricted else None


def inspect_preferences(params, blockers):
    managed = params['preferences_path']
    present, allowlist, overlaps = False, None, []
    for path in [PREFERENCES] + apt_files(PREFERENCES_DIR, PREFERENCE_NAME):
        if not os.path.lexists(path):
            continue
        try:
            text = read_text(path) if os.path.isfile(path) else ''
        except OSError as error:
            blockers.append('%s: cannot be read: %s' % (path, error))
            continue
        if path == managed:
            if is_regular(path) and text.splitlines()[:1] == [params['marker']]:
                present, allowlist = True, managed_allowlist(text)
            else:
                blockers.append('%s: exists but was not written by this '
                                'role; it is neither replaced nor removed'
                                % path)
            continue
        for start, fields in pref_stanzas(text):
            reason = backports_overlap(fields)
            if reason:
                overlaps.append('%s: the record at line %d: %s'
                                % (path, start, reason))
    return present, allowlist, overlaps


def refresh_pending(params, blockers):
    """Accept only no marker or the role's own regular marker file."""
    path = params['refresh_flag_path']
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if (stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and read_text(path) == params['marker'] + '\n'):
        return True
    blockers.append('%s: not the marker this role writes; it is neither '
                    'overwritten nor removed' % path)
    return False


def run(module, argv):
    return module.run_command(argv, check_rc=True,
                              environ_update={'LC_ALL': 'C'})[1]


def source_key(uri, suite, component):
    if uri == '/var/lib/dpkg/status':
        return 'status'
    archive = archive_of(uri)
    return (archive, suite, component) if archive and suite else 'other'


def parse_policy(text, native):
    """Return {package: {'installed', 'ambiguous', 'versions'}}.

    'versions' maps a version to (its priority, [(source, file priority)]).
    """
    packages, package, version = {}, None, None
    for line in text.splitlines():
        if line and not line[0].isspace():
            package, version = None, None
            if line.endswith(':'):
                name = line[:-1]
                for suffix in (':' + native, ':all'):
                    if name.endswith(suffix):
                        name = name[:-len(suffix)]
                if name in packages:
                    packages[name]['ambiguous'] = True
                package = packages.setdefault(name, {
                    'installed': None, 'ambiguous': False, 'versions': {}})
            continue
        if package is None:
            continue
        stripped = line.strip()
        if stripped.startswith('Installed:'):
            package['installed'] = stripped.split(':', 1)[1].strip()
            continue
        if stripped.startswith(('Candidate:', 'Version table:')):
            continue
        match = POLICY_VERSION.match(line)
        if match:
            version = match.group(1)
            if version in package['versions']:
                package['ambiguous'] = True
            package['versions'][version] = (int(match.group(2)), [])
            continue
        match = POLICY_SOURCE.match(line)
        if match and version is not None:
            priority, uri, suite, component = match.groups()
            package['versions'][version][1].append(
                (source_key(uri, suite, component), int(priority)))
        elif stripped:
            package['ambiguous'] = True
    return packages


def is_backports(source):
    return source not in ('other', 'status') and source[1] == BACKPORTS


def post_priority(base, priority, sources, lost, allow):
    """Priority of a version after the change, or None if indeterminate.

    Backports gets the proposed managed restriction. Elsewhere the change
    keeps the preferences, so a version priority that differs from the best
    file priority comes from a package-specific pin. It stays only if none of
    the version's sources is lost, because the pin may name a lost release;
    otherwise the result is accepted only if it does not depend on which
    source set it.
    """
    retained = [(source, file_priority) for source, file_priority in sources
                if source != 'status' and source not in lost]
    backports = [source for source, _ in retained if is_backports(source)]
    if backports:
        if len(backports) != len(retained):
            return None
        return UPDATE_PRIORITY if base in allow else -1
    if priority != max(file_priority for _, file_priority in sources):
        if any(source in lost for source, _ in sources):
            return None
        return priority
    best = max(file_priority for _, file_priority in retained)
    if best == priority or (best >= UPDATE_PRIORITY) == (
            priority >= UPDATE_PRIORITY):
        return min(best, priority)
    return None


def update_loss(base, version, versions, lost, allow, newer_or_equal):
    """Why the installed version may lose its update source, or None.

    Called only while sources or backports eligibility are being removed.
    """
    status_only = all(source == 'status'
                      for source, _ in versions[version][1])
    touched = any(source in lost for _, sources in versions.values()
                  for source, _ in sources)
    if not touched and not status_only:
        return None

    def current(candidate):
        return candidate == version or newer_or_equal(candidate, version)

    indeterminate, refused = False, False
    for candidate, (priority, sources) in versions.items():
        if not any(source != 'status' and source not in lost
                   for source, _ in sources):
            continue
        post = post_priority(base, priority, sources, lost, allow)
        if post is not None and post < UPDATE_PRIORITY:
            refused = refused or current(candidate)
        elif current(candidate):
            if post is not None:
                return None
            indeterminate = True

    hints = ' or '.join(sorted({keep_hint(source, base) for source in lost}))
    unavailable = sorted({source for candidate, (_, sources)
                          in versions.items() for source, _ in sources
                          if source in lost and current(candidate)})
    if indeterminate:
        return ('indeterminate: existing preferences decide whether a '
                'remaining source offers this or a newer version; review '
                'them, or %s' % hints)
    if refused:
        return ('prohibited: the remaining sources offer this or a newer '
                'version only below priority %d after the change; %s'
                % (UPDATE_PRIORITY, hints))
    if unavailable:
        return ('unavailable: only %s offers this or a newer version; %s, '
                'or remove the package first'
                % (', '.join(describe(source) for source in unavailable),
                   hints))
    return ('uncertain provenance: the installed version is known only from '
            'the dpkg status and no remaining source provably offers it or a '
            'newer one; establish its origin, or %s' % hints)


def check_evidence(module, removed, narrowed, current, allow, old_eligible,
                   blockers):
    """Block if an installed package could lose its update source."""
    indexed = set()
    for line in run(module, ['apt-cache', 'policy']).splitlines():
        match = POLICY_FILE.match(line)
        key = match and source_key(*match.groups())
        if isinstance(key, tuple):
            indexed.add(key)
    backports = {source for source in current if source[1] == BACKPORTS}
    needed = set(removed) | (backports if narrowed else set())
    missing = sorted(needed - indexed)
    for source in missing:
        blockers.append(
            '%s: no local index, so the installed packages that rely on it '
            'cannot be determined; refresh the indexes with the current '
            'sources first, or %s' % (describe(source), keep_hint(source)))
    if missing:
        return

    native = run(module, ['dpkg', '--print-architecture']).strip()
    installed = {}
    for line in run(module, [
            'dpkg-query', '--show', '--showformat=${Package}\\t'
            '${Architecture}\\t${Version}\\t${db:Status-Abbrev}\\n',
    ]).splitlines():
        fields = line.split('\t')
        if len(fields) != 4:
            blockers.append('dpkg-query output was not recognized: %r' % line)
            return
        name, arch, version, status = fields
        if len(status) > 1 and status[1] not in 'nc':
            key = name if arch in (native, 'all') else '%s:%s' % (name, arch)
            installed[key] = (name, version)
    if not installed:
        return

    compared = {}

    def newer_or_equal(left, right):
        """dpkg --compare-versions on the host; rc 1 is false, others fail."""
        if (left, right) not in compared:
            rc, _, err = module.run_command(
                ['dpkg', '--compare-versions', left, 'ge', right],
                environ_update={'LC_ALL': 'C'})
            if rc not in (0, 1):
                module.fail_json(msg='dpkg --compare-versions %s ge %s '
                                 'failed: %s' % (left, right, err.strip()))
            compared[(left, right)] = rc == 0
        return compared[(left, right)]

    policy = parse_policy(
        run(module, ['apt-cache', 'policy'] + sorted(installed)), native)
    for key, (base, version) in sorted(installed.items()):
        info = policy.get(key)
        if (not info or info['ambiguous'] or info['installed'] != version
                or version not in info['versions']):
            blockers.append(
                'package %s %s: apt-cache policy gave no single matching '
                'record, so its update source cannot be checked'
                % (key, version))
            continue
        loses_backports = narrowed and base not in allow and (
            old_eligible is None or base in old_eligible)
        lost = removed | (backports if loses_backports else set())
        reason = lost and update_loss(base, version, info['versions'], lost,
                                      allow, newer_or_equal)
        if reason:
            blockers.append('package %s %s: %s' % (key, version, reason))


def main():
    module = AnsibleModule(
        argument_spec={
            'components': {'type': 'list', 'elements': 'str',
                           'required': True},
            'backports_packages': {'type': 'list', 'elements': 'str',
                                   'required': True},
            'sources_path': {'type': 'path', 'required': True},
            'sources_content': {'type': 'str', 'required': True},
            'preferences_path': {'type': 'path', 'required': True},
            'marker': {'type': 'str', 'required': True},
            'refresh_flag_path': {'type': 'path', 'required': True},
        },
        supports_check_mode=True,
    )
    params = module.params
    allow = set(params['backports_packages'])
    blockers = []

    current, remove, stub, report = inspect_sources(params, blockers)
    pref_present, pref_allow, overlaps = inspect_preferences(params, blockers)
    if allow:
        blockers.extend('%s; it may override the backports policy of this '
                        'role, so resolve it manually' % overlap
                        for overlap in overlaps)
    pending = refresh_pending(params, blockers)

    suites = ['trixie', 'trixie-updates', 'trixie-security']
    if allow:
        suites.append(BACKPORTS)
    desired = {(SUITE_ARCHIVE[suite], suite, component)
               for suite in suites for component in params['components']}
    removed = current - desired

    # Packages that may follow backports today: none without the source,
    # the previous allowlist of the role's own file, otherwise all.
    old_eligible = None
    if not any(source[1] == BACKPORTS for source in current):
        old_eligible = set()
    elif pref_allow is not None and not overlaps:
        old_eligible = pref_allow
    narrowed = old_eligible is None or bool(old_eligible - allow)

    if not blockers and (removed or narrowed):
        check_evidence(module, removed, narrowed, current, allow,
                       old_eligible, blockers)

    managed = params['sources_path']
    unchanged = (is_regular(managed)
                 and read_text(managed) == params['sources_content'])
    module.exit_json(
        changed=False,
        blockers=blockers,
        migration_report=report,
        remove_paths=remove,
        stub_sources_list=stub,
        sources_change=not unchanged or bool(remove) or stub,
        preferences_managed=pref_present,
        refresh_pending=pending,
        sources_dir_exists=os.path.isdir(os.path.dirname(managed)),
        preferences_dir_exists=os.path.isdir(
            os.path.dirname(params['preferences_path'])),
    )


if __name__ == '__main__':
    main()
