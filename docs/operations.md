# Operations

Everything here assumes the `home-cluster` conventions: Flux reconciles from
`main`, secrets go through SOPS, and nothing is applied with `kubectl apply`.

## First deployment

1. **Create the Secret.** The one step that cannot happen in CI, because the age
   key is deliberately not in any repository. Run it wherever the key lives:

   ```bash
   ./scripts/make-secret.sh
   git add k8s/secret.sops.yaml && git commit -m "Add the deployment secret"
   ```

   Nothing here needs `k8s/kustomization.yaml` to be reachable until this file
   exists: it is listed as a resource, so `kustomize build` fails outright
   without it and the Flux Kustomization never goes Ready. That is deliberate —
   a deployment missing its credentials should not half-start.

   The script exists to remove two ways of getting this wrong by hand. The
   database password appears twice — as `postgres-password` and inside
   `database-url` — and PostgreSQL simply refuses the connection if they drift
   apart. And a plaintext secret must not survive a failed encryption long
   enough to be committed, so the script deletes it on any error path.

   **To rotate a value afterwards, edit in place** — `sops k8s/secret.sops.yaml`.
   Re-running the script would mint a new database password while PostgreSQL
   still holds the old one in its PVC, and the API would fail to connect with an
   authentication error that looks nothing like its cause.

2. **Build the image.** Tag a release; the workflow builds `linux/arm64` and
   pushes to GHCR.

   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```

3. **Pin the tag** in `k8s/deployment.yaml` and `k8s/verify-cronjob.yaml`, then
   push. Flux rolls it out within a minute.

4. **Add the Cloudflare Access application** for `medvault.minipi.net` in
   `terraform/cloudflare/`. Until that exists the pod is reachable through the
   tunnel with no authentication in front of it, and the application trusts the
   header Access is supposed to set. This is the step to get right.

5. **Create the household and the subject:**

   ```bash
   kubectl -n medical-vault exec deploy/medical-vault -- \
     medvault tenant-add pan-household --name "Pan Household" --owner you@example.com
   kubectl -n medical-vault exec deploy/medical-vault -- \
     medvault subject-add pan-household self --name "Pan Yan Bin" \
       --birth-date 1995-03-02 --sex male --also-known-as "潘彦斌"
   kubectl -n medical-vault exec deploy/medical-vault -- medvault reindex
   ```

## Authentication

Identity comes from Cloudflare Access, which authenticates at the edge and sets
`Cf-Access-Authenticated-User-Email`, stripping any copy the client sent.

`MEDVAULT_TRUST_ACCESS_HEADER` gates whether that header is believed. It is
**off by default**, so a deployment that accidentally exposes the service
without Access in front of it rejects every request rather than believing
whatever the client claims. The manifests turn it on because the tunnel is the
only route to the pod. If that ever stops being true, turn it off first.

Membership lives in the vault's `tenant.json`, not only in the database, so who
may read what survives a database rebuild along with the records.

```bash
medvault member-add pan-household partner@example.com --role viewer
```

Roles are `owner`, `editor` and `viewer`; only the first two can write.

## Routine maintenance

### Adding measurements the catalogue does not know

```bash
kubectl -n medical-vault exec deploy/medical-vault -- medvault stats
```

It prints every printed label with no catalogue entry, most frequent first. Add
them to `packages/api/medvault/catalog/analytes.yaml`, bump the `version` at the
top of that file, ship it, and reindex. Every historical observation using those
labels becomes a proper series — no migration, no re-upload.

### Integrity

A CronJob runs `medvault verify` nightly, re-hashing every original against the
digest recorded when it was filed. A non-zero exit fails the Job, which shows up
in `kubectl get jobs -A` and in the kube-state metrics Prometheus already
scrapes.

This is aimed at silent corruption, which backups do not protect you from: a
rotted file gets backed up too, and by the time you notice, every generation of
the backup has the rotted copy.

```bash
kubectl -n medical-vault get cronjob medical-vault-verify
kubectl -n medical-vault logs job/<name>
```

## Recovery

### The database is broken or lost

Not an incident. Delete it and let it rebuild:

```bash
kubectl -n medical-vault delete deploy postgres
kubectl -n medical-vault delete pvc medical-vault-db
# re-apply by forcing a reconcile; the init container recreates the schema
flux reconcile kustomization medical-vault --with-source
```

The init container runs `medvault init-db && medvault reindex` on every start,
so the projection repopulates from the vault by itself. This path is exercised
on every deploy rather than trusted.

### The vault is lost

This is the incident. Restore the PVC from the nightly R2 backup — see
`docs/runbooks/backup-restore.md` in `home-cluster` — then reindex.

Verify the restore before trusting it:

```bash
kubectl -n medical-vault exec deploy/medical-vault -- medvault verify
```

Check the destination, not the client: a missing `nfs-common` once made a failed
NFS mount look like a successful write because `touch` silently wrote to the
local mountpoint.

### Getting the data out

```bash
kubectl -n medical-vault exec deploy/medical-vault -- medvault export /tmp/export
kubectl -n medical-vault cp medical-vault-<pod>:/tmp/export ./export
```

NDJSON and CSV, in formats nothing here owns. The originals are not copied —
they are in the vault, and `kubectl cp` of the whole PVC is the way to take
those. A record you cannot get out of a system is a record that system controls.

## Things not to do

- **Do not `kubectl apply` or `kubectl edit` anything in this namespace.** Flux
  reverts it at the next reconcile and the discrepancy is confusing to debug.
- **Do not scale the API deployment above one replica.** The vault PVC is
  ReadWriteOnce, and two pods appending to one document directory over NFS is
  the exact failure this design exists to prevent.
- **Do not move the vault PVC to `local-path`** to make it faster. That is one
  node's SD card: not backed up, does not survive a node rebuild, and worn out
  by sustained writes.
- **Do not edit files inside the vault by hand.** Corrections go through the API
  so they are recorded as superseding documents. A hand-edit breaks the SHA-256
  in the envelope, and the nightly verify will — correctly — start failing.
- **Do not commit `k8s/secret.sops.yaml` unencrypted.** CI refuses it, but CI
  runs after the commit exists; a key in git history is not removed by deleting
  it later.
