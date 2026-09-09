# Wave 8 Secure Media Contract

Approved by Cze Yik on 9 September 2026.

## Launch policy

- Accept JPEG and PNG images up to 5 MB, and MP4 and 3GP videos up to 16 MB.
- Keep automated image/video analysis disabled. Media never enters PostgreSQL or an LLM.
- Store clean objects in one private Amazon S3 bucket in AWS Malaysia (`ap-southeast-5`). Grant the
  EC2 instance profile only the required object-prefix actions instead of creating access keys.
- Scan every object with ClamAV before it receives an `approved/` object key.
- Permit review only through an active named-administrator session. Each request is audited and
  redirects to an object URL that expires after five minutes.
- Immediately discard rejected and failed bytes. The retained database row contains status and
  integrity metadata but no media content or download URL.
- Delete approved media with its ticket. Wave 10 will schedule that operation 36 months after
  ticket closure and cover backups and legal holds.

## Processing boundary

A valid Meta webhook reserves the provider message ID and creates one queued attachment. The
webhook does not download media. The worker obtains metadata and a temporary URL from Graph API
`v26.0`, authenticates both requests, streams into a quarantined temporary file, checks the byte
limit, verifies Meta's SHA-256 where supplied, identifies and structurally validates the content,
and scans it with ClamAV's `INSTREAM` protocol. Only then is it written under a generated object
key and linked to the conversation's ticket. Temporary scanner or storage failures retry three
times with bounded backoff; exhausted failures remain inaccessible.

The bucket must use S3 Block Public Access and server-side encryption. The EC2 instance role uses
temporary credentials and grants access only to the required media and backup prefixes.

## External references verified 9 September 2026

- Meta WhatsApp media retrieval and supported-media documentation:
  `https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media`
- Amazon S3 security best practices:
  `https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html`
- EC2 IAM roles:
  `https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html`
- ClamAV `INSTREAM` protocol:
  `https://docs.clamav.net/manual/Usage/ClamdProtocol.html`
