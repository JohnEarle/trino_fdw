# CloudNativePG deployment

```bash
kubectl create namespace trino-bridge
kubectl -n trino-bridge create secret generic trino-fdw-creds \
    --from-file=password=./trino-password.txt \
    --from-file=ca.pem=./trino-ca.pem
kubectl -n trino-bridge apply -f bootstrap-sql.yaml -f networkpolicy.yaml -f cluster.yaml
```

Example manifests for running the image on CloudNativePG. Use a released image (`ghcr.io/<owner>/trino_fdw:17-v<version>`, ideally by
digest) or build your own, and set `spec.imageName` in `cluster.yaml`. These
files are placeholders; keep the real host, catalog and secrets in your own
infrastructure repository. `bootstrap-sql.yaml` holds the SQL run
once at cluster creation; edit the host, catalog and schema in it.
