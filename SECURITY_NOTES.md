# Security Notes

Date: 2026-05-26

## Repository Scan Summary

Scanned for:

- API keys and tokens
- passwords
- certificates and private keys
- webhook URLs
- local personal paths
- generated logs and reports
- client or customer data

No real API keys, tokens, passwords, certificates, or private keys were found.

## Sensitive Metadata Found

A generated capture manifest contained a local personal Windows path. It was treated as personal metadata and the generated session output is excluded from the repository.

No secret value is reproduced here.

## Application Security Notes

- NB Capture records screens, optional microphone audio, and optional webcam video. Users should be warned not to record sensitive information unintentionally.
- The app launches `ffmpeg` through `subprocess` without `shell=True`, which reduces command injection risk.
- `PyAutoGUI` is present as a screenshot fallback dependency. It should not be extended to automate user input without explicit review.

## GitHub Settings to Enable Manually

Enable these in the GitHub repository settings after publication:

- Secret scanning
- Push protection for secrets
- Dependabot alerts
- Dependabot security updates
- Code scanning alerts for CodeQL
- Branch protection requiring CI before merge

## Revocation Guidance

No credential was found that requires revocation. If future scans detect a secret, revoke it at the provider immediately and rotate affected credentials before publishing history.
