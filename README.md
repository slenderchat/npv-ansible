# npv-ansible

**English** | [Русский](README.ru.md)

Ansible configuration for the Debian 13 hosts of the npv service.

## Current state

This repository currently contains:

- the static inventory: [`inventory/hosts.yml`](inventory/hosts.yml);
- the `timezone` role: [`roles/timezone`](roles/timezone);
- the `hosts` role: [`roles/hosts`](roles/hosts);
- the `hostname` role: [`roles/hostname`](roles/hostname);
- the collection requirements: [`collections/requirements.yml`](collections/requirements.yml).

Host groups:

- `ingress` — entry nodes of the npv service.
- `egress` — exit nodes of the npv service.

Connection settings shared by all hosts are defined in `all.vars`.

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

## Planned playbooks

None of these playbooks exist yet:

- `new_server.yml` — base OS configuration for all managed hosts.
- A separate playbook for ingress nodes (name not yet chosen).
- A separate playbook for egress nodes (name not yet chosen).

## Secrets

Secrets belong in Ansible Vault and must never be committed in plain text.

## Copyright

See [COPYRIGHT](COPYRIGHT).
