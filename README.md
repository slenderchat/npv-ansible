# npv-ansible

**English** | [Русский](README.ru.md)

Ansible configuration for the Debian 13 hosts of the npv service.

## Current state

This repository currently contains only the static inventory: [`inventory/hosts.yml`](inventory/hosts.yml).

Host groups:

- `ingress` — entry nodes of the npv service.
- `egress` — exit nodes of the npv service.

Connection settings shared by all hosts are defined in `all.vars`.

To inspect the inventory locally:

```sh
ansible-inventory -i inventory/hosts.yml --graph
```

## Planned playbooks

None of these playbooks exist yet:

- `new_server.yml` — base OS configuration for all managed hosts.
- A separate playbook for ingress nodes (name not yet chosen).
- A separate playbook for egress nodes (name not yet chosen).

## Secrets

Secrets belong in Ansible Vault and must never be committed in plain text.

## Copyright

See [COPYRIGHT](COPYRIGHT).
