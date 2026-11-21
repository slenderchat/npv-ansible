# npv-ansible

**English** | [Русский](README.ru.md)

Ansible configuration for the Debian 13 hosts of the npv service.

## Current state

This repository currently contains:

- the static inventory: [`inventory/hosts.yml`](inventory/hosts.yml);
- the `timezone` role: [`roles/timezone`](roles/timezone);
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

## Planned playbooks

None of these playbooks exist yet:

- `new_server.yml` — base OS configuration for all managed hosts.
- A separate playbook for ingress nodes (name not yet chosen).
- A separate playbook for egress nodes (name not yet chosen).

## Secrets

Secrets belong in Ansible Vault and must never be committed in plain text.

## Copyright

See [COPYRIGHT](COPYRIGHT).
