# Retained representative risk evidence

`official-risk-evidence-2022-2025-v1.zip` is the immutable input used by the
GitHub-hosted representative evaluation. It contains the raw official Federal
Reserve, SEC, and BLS pages plus normalized evidence and its schema-v2 manifest.

- Acquisition date: 2026-09-05 UTC
- Coverage: `[2022-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`
- Raw official artifacts: 99
- Normalized records: 1,067
- Declared conservative gaps: 14
- ZIP SHA-256: `4dd11668fae3194c3643e02b48eea378699821ba5aca845d8de60eddf52ebd71`
- Internal evidence-manifest SHA-256:
  `d52e864d398c08b1694d3278b62d5fe34608482052893c5a813dfb8167423967`

The internal manifest records every source URL, retrieval timestamp, raw-page
SHA-256, normalized record, and gap. The derivation command validates those
bindings before producing any point-in-time risk state. Do not replace this
archive in place: a refresh requires a new dataset/version, hashes, review, and
an unseen evaluation policy.
