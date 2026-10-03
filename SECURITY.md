# Security policy

## Supported versions

Only the latest release receives fixes. Security fixes go into a new
release of the current major version.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Report them
privately through GitHub instead: on the
[Security tab](https://github.com/Tom-Joad/ucg-config-backup/security),
choose **Report a vulnerability**. You will get an answer within a few days.

## Scope notes

This tool holds the login of a network console and downloads its full
configuration, so the following are of particular interest:

- `UCG_PASSWORD` or the webhook URL (its path is usually a secret) ending up
  in logs, the webhook payload, the image or on disk
- backup files readable by users they shouldn't be readable by
- path handling that could write or delete outside `/backups`

The container runs the backup as a non-root user (`PUID:PGID`) and needs no
inbound ports.

## Supply chain

Images are built by GitHub Actions from version tags only. Third-party
actions are pinned to commit SHAs. Dependencies are checked with `pip-audit`,
and the repository is scanned with `gitleaks`. Images are scanned with Trivy
and signed keylessly with cosign. To verify an image (needs cosign 3.x for
releases after 2.0.0; 2.0.0 and older also verify with cosign 2.x):

```bash
cosign verify ghcr.io/tom-joad/ucg-config-backup:latest \
  --certificate-identity-regexp 'https://github.com/Tom-Joad/ucg-config-backup/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
