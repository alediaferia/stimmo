# Security

If you discover a security issue in stimmo, please email
**me@alediaferia.com** rather than opening a public GitHub issue.

You can expect an acknowledgement within 7 days. We will work with you on
disclosure timing and credit.

## Scope

stimmo is a small static-data + form web app with no authentication, no
persistent storage, and no user accounts. Reports we are most interested in:

- Server-side request forgery via the `/api/geocode`, `/import`, or
  `/estimate` endpoints.
- Cross-site scripting via Jinja templates.
- Denial-of-service vectors through unbounded input parsing.
- Vulnerabilities in dependencies that materially affect stimmo.
