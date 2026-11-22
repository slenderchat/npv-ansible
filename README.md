# npv-ansible

**English** | [Русский](README.ru.md)

Ansible configuration for the Debian 13 hosts of the npv service.

## Current state

This repository currently contains:

- the static inventory: [`inventory/hosts.yml`](inventory/hosts.yml) and role variables under [`inventory/group_vars`](inventory/group_vars);
- the `timezone` role: [`roles/timezone`](roles/timezone);
- the `hosts` role: [`roles/hosts`](roles/hosts);
- the `hostname` role: [`roles/hostname`](roles/hostname);
- the `dns_resolver` role: [`roles/dns_resolver`](roles/dns_resolver);
- the `locale` role: [`roles/locale`](roles/locale);
- the `chrony` role: [`roles/chrony`](roles/chrony);
- the collection requirements: [`collections/requirements.yml`](collections/requirements.yml).

Host groups:

- `ingress` — entry nodes of the npv service.
- `egress` — exit nodes of the npv service.

Connection settings shared by all hosts are defined in `all.vars`.

Inventory convention: connection variables such as `ansible_user`, `ansible_host` and `ansible_port` may stay in `inventory/hosts.yml`. Role variables for a group go to `inventory/group_vars/<group_name>/<role_name>.yml`, and role variables for a host go to `inventory/host_vars/<inventory_hostname>/<role_name>.yml`, where the directory name is the exact inventory hostname, for example `ingress-ru-tweb-msk1-01.sl-chat.ru`.

To inspect the inventory locally:

```sh
ansible-inventory -i inventory/hosts.yml --graph
```

## Collections

Install the required collections on the controller:

```sh
ansible-galaxy collection install -r collections/requirements.yml
```

## Roles

### `timezone`

Sets the system timezone of a managed host to `Europe/Moscow` using `community.general.timezone` with only the `name` parameter. The hardware clock and NTP are not managed by this role.

On systemd hosts the module reads and sets the timezone through `timedatectl`, so it needs a working `timedatectl` and the `tzdata` zone file `/usr/share/zoneinfo/Europe/Moscow` on the managed host. `hwclock` (package `util-linux-extra`) is needed only if the module falls back to its non-systemd path because `timedatectl` is missing or unusable.

Documented module behavior:

- if the host already uses `Europe/Moscow`, the task reports no change, including with `--check`;
- with `--check`, a different timezone is reported as a change, and nothing is changed on the host;
- with `--diff`, alone or combined with `--check`, the module shows its own before/after values of `name`.

### `hosts`

Renders the complete `/etc/hosts` from a template (owner `root:root`, mode `0644`) and keeps it immutable (`chattr +i`) between runs. Any lines added outside the template, for example by the provider, are removed. The system hostname, DNS resolver configuration and cloud-init settings are not managed by this role.

Managed entries:

- `127.0.0.1 localhost`;
- `127.0.1.1 <short-name> <fqdn>`, where `<fqdn>` is `inventory_hostname` and `<short-name>` is its first label; the current system hostname, network facts and DNS are not used;
- the standard Debian IPv6 entries: `::1 localhost ip6-localhost ip6-loopback`, `ff02::1 ip6-allnodes`, `ff02::2 ip6-allrouters`;
- optional records from `hosts_extra_records` (default `[]`), rendered after the standard entries in the given order:

  ```yaml
  hosts_extra_records:
    - address: 192.0.2.10
      names:
        - example-01.example.org
        - example-01
  ```

Behavior:

- `/etc/hosts` must be absent or a regular file; any other type, such as a symlink, fails the role before any change. For an existing file the role reads the flags with `lsattr` on every run, including on a converged host; a converged run reports no change and performs no rewrite or `chattr` operation.
- When an update is needed, the role temporarily clears `i` if it is set. Content changes use atomic replacement; owner, group and mode are corrected without replacing the file. If only `i` is missing, it is set without rewriting the file.
- After setting `i`, the role reads the flags back and fails if `i` is missing. If an update fails after `i` was cleared, the role attempts to restore `i` and reports the original error together with the verification result.

Requires `lsattr` and `chattr` (package `e2fsprogs`) and a filesystem that supports the immutable attribute on the managed host. While `i` is set, ordinary writes to `/etc/hosts`, including by cloud-init or provider agents, are blocked.

Check and diff:

- with `--check`, the role inspects the current state and predicts changes without modifying `/etc/hosts` or its attributes;
- with `--diff`, content drift produces one before/after content diff; an attribute-only change has no content diff.

### `hostname`

Sets the system hostname with `ansible.builtin.hostname` and the `systemd` strategy, which uses `hostnamectl` to set the static and transient hostname.

The hostname is the first label of the inventory FQDN, `inventory_hostname_short`; for example, `ingress-ru-tweb-msk1-01.sl-chat.ru` gets the hostname `ingress-ru-tweb-msk1-01`. `/etc/hosts` is managed by the `hosts` role, not by this role.

Check mode and diff mode are supported natively by the module: with `--check` a different hostname is reported as a change without modifying the host, and with `--diff` the module shows the hostname before and after.

### `dns_resolver`

Makes applications resolve through the local systemd-resolved stub at `127.0.0.53`. Public DNS goes only to the host group's DNS-over-TLS upstreams. Networkd, netplan, cloud-init and interface state are not changed.

The upstreams are an ordered list in the required group variable `dns_resolver_upstreams`. Each entry uses the systemd-resolved syntax `IPv4:port#tls-name`; port 53 is rejected, because systemd-resolved stores 53 and 853 alike as port 0, which means 853 under strict DNS-over-TLS. The role has no default and fails before any change if the list is missing or malformed. The values are defined in `inventory/group_vars/ingress/dns_resolver.yml` and `inventory/group_vars/egress/dns_resolver.yml`:

| Group | `dns_resolver_upstreams` |
| --- | --- |
| `ingress` | `77.88.8.88:853#safe.dot.dns.yandex.net`, `77.88.8.2:853#safe.dot.dns.yandex.net`, `185.191.32.7:853#public.sdns.bi.zone`, `185.191.32.8:853#public.sdns.bi.zone` |
| `egress` | `9.9.9.9:853#dns.quad9.net`, `149.112.112.112:853#dns.quad9.net`, `1.1.1.2:853#security.cloudflare-dns.com`, `1.0.0.2:853#security.cloudflare-dns.com` |

The drop-in `/etc/systemd/resolved.conf.d/50-dns_resolver.conf` resets the inherited `DNS=` and `Domains=` lists and sets `DNS=<whole list>`, `Domains=~.`, `DNSOverTLS=yes`, `DNSStubListener=yes`, `LLMNR=no` and an empty `FallbackDNS=`. systemd-resolved switches between the listed servers itself and does not return to the first one immediately. Encryption and certificate checks are strict: there is no plaintext fallback. DNS servers received over DHCP may still appear on a link; they are not used while the global `~.` applies.

`/etc/resolv.conf` becomes a regular `root:root` file with mode `0644` and the immutable flag (`chattr +i`). It contains only `nameserver 127.0.0.53`, `options edns0 trust-ad` and `search .`. The previous file, symlink or missing path is replaced.

Order of a normal run:

1. If `e2fsprogs` (`lsattr`, `chattr`) is missing, install it first, while the original resolver still works; this changes no resolver state. Then record the original state: the type of `/etc/resolv.conf` (regular file, symlink or missing; anything else fails), its `i` flag (a failing `lsattr` fails the run; the flag is never assumed clear), its contents (through a symlink as well), the drop-in with its owner, group and mode, the package states, and whether systemd-resolved is running and enabled.
2. Install the remaining missing packages (`systemd-resolved`, `bind9-dnsutils`, `ca-certificates`, without recommends) while the original resolver still works. On a first installation the Debian package can replace an unlocked `/etc/resolv.conf` with a symlink to the stub and start the service; the path is inspected again afterwards.
3. Write the drop-in. If it changed, restart systemd-resolved; then make sure the service is enabled and running. An unchanged, running service is not restarted.
4. Read the effective settings over D-Bus with `busctl` and fail, with the conflicting value and where to look, if public queries could leave the approved list. Servers are compared by address, port and TLS name in the inventory order; a stored port 0 counts as 853 because `DNSOverTLS=yes` is verified in the same step, and any other unexpected port is a mismatch. Examples: a later drop-in changes `DNS=`, global domains are not exactly `~.`, a network link has its own DNS domain, the current server is not in the list, `FallbackDNS=` is not empty, or `DNSOverTLS`, `LLMNR` or `DNSStubListener` differ. Any approved secondary being the current server is fine.
5. Query `deb.debian.org.` (type A) with `dig` directly at `127.0.0.53`, with a bounded number of retries. When the run changes or repairs something, the systemd-resolved cache is flushed first. On a converged host the cache is left alone, so the answer may come from it. Success confirms the configured resolver path, not that every upstream answered.
6. Only then replace `/etc/resolv.conf` atomically, set `i` and read the flags back. A regular file is replaced by the template module. A symlink, including a dangling one or one whose target already has the desired text, is never written through: the complete file is staged as `/etc/.resolv.conf.dns_resolver.tmp` with `root:root 0644` and renamed over the link; the staging file is removed if a step fails. On a converged host nothing is rewritten, `chattr` is not run and no service is restarted.

If any step after step 1 fails, the role restores what differs from the recorded state and then fails with the original error and the result of each recovery step:

- the drop-in (or removes it if it was absent);
- the service state: restarted with the restored configuration if it was running, stopped if it was not; for a unit that existed before the run, an `enabled` or `disabled` state changed by the run is restored as well, while other states such as `masked` are never changed and nothing is unmasked. A unit installed by this run stays installed and enabled, and the report says so;
- the original regular file with its owner, mode and `i` flag (replacing a symlink through the same staged rename), the original symlink, or the missing path.

Each restored item is read back, and only a verified result is reported as restored.

If an original symlink no longer leads to its original contents (for example, the target belonged to a resolver that the package installation removed, or it is a systemd-resolved file while the service is stopped), the saved contents replace the link as a regular file through the staged rename, and this is reported. After a recovery the role checks resolution once through NSS (`getent`). Packages are never removed.

Check and diff:

- with `--check`, the role reads the current state and predicts the package installation, the drop-in directory and file, the restart or activation, the replacement of a symlink by a regular file and the `i` flag. Without `lsattr` the prerequisite installation is predicted and the current `i` flag of a regular `/etc/resolv.conf` is reported as unknown; the run does not fail only for that. It does not start services, flush caches or send DNS queries, and the runtime verification is reported as deferred. On a host without the package, the planned installation and activation are shown as messages, because apt cannot refresh its lists and the unit does not exist yet.
- with `--diff`, the drop-in and `/etc/resolv.conf` show one content diff each; for a symlink the "before" side is the target's contents, and for a dangling symlink it is empty.

### `locale`

Sets the persistent system default locale `LANG=C.UTF-8` in `/etc/locale.conf` with a single `ansible.builtin.lineinfile` task. The file is created if it is absent and kept as `root:root 0644`. An existing active `LANG=` line, quoted or not, is replaced; `LANGUAGE`, `LC_*` and commented lines, as well as other settings and comments, are kept. Locale generation and running sessions are not managed by this role.

Check mode and diff mode are supported natively by the module: with `--check` a missing or different `LANG` is reported as a change without modifying the host, and with `--diff` the module shows the file contents before and after.

### `chrony`

Ensures that the `chrony` package is installed and `chrony.service` is enabled and started, using `ansible.builtin.apt` and `ansible.builtin.systemd_service`. The package configuration and time sources are kept; a converged run reports no change or restart. A running daemon alone does not verify clock synchronization.

With `--check` the role reads only the local APT metadata and does not refresh it: if chrony is installed or has an installation candidate there, apt simulates the installation and, with `--diff`, shows the package transaction; otherwise the installation is reported as a change whose transaction is unknown until APT metadata is available. If `chrony.service` does not exist yet, its planned activation is shown as a message; an existing unit is evaluated by the module.

## Planned playbooks

None of these playbooks exist yet:

- `new_server.yml` — base OS configuration for all managed hosts.
- A separate playbook for ingress nodes (name not yet chosen).
- A separate playbook for egress nodes (name not yet chosen).

## Secrets

Secrets belong in Ansible Vault and must never be committed in plain text.

## Copyright

See [COPYRIGHT](COPYRIGHT).
