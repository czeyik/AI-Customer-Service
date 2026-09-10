# Secure Media Policy

Approved by Cze Yik on 9 September 2026.

- Accept JPEG/PNG up to 5 MB and MP4/3GP up to 16 MB.
- Keep automated analysis disabled. Never store bytes in PostgreSQL or send media to a model.
- Use private encrypted S3 in `ap-southeast-5` through the EC2 role. Enable Block Public Access.
- A valid Meta webhook queues one attachment without downloading it. The worker authenticates Graph
  API `v26.0`, streams to quarantine, enforces size, verifies SHA-256 when supplied, validates file
  structure, and scans with ClamAV `INSTREAM` before assigning an `approved/` key.
- Discard rejected/failed bytes. Preserve inaccessible metadata for bounded retry; after three failed
  attempts, keep the attachment inaccessible.
- Only active named admins receive audited five-minute review links.
- Delete approved media with its ticket after 36 months, subject to legal hold. Object deletion must
  succeed before database deletion.

Evidence: `tests/test_media_pipeline.py`, `tests/test_media_integrations.py`, and
`tests/test_data_lifecycle.py`.
