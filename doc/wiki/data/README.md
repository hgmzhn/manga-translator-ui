# Related projects data

`related-projects.yml` is the reviewed source for the Wiki related-projects list. Additions are accepted through pull requests, but submitting an entry does not publish it automatically.

Each project must include a stable ID, bilingual name, description and relationship, an HTTPS public URL, category, logo URL and authorization, official HTTPS contact URL, license status, review status, and ISO `YYYY-MM-DD` last-check date. Do not put personal email addresses, API keys, tracking links, or unverified logo assets in this file.

Run `uv run python doc/wiki/verify_related_projects.py` from the repository root before submitting a change. Only entries with `approval_status: approved` may be published; maintainers must manually confirm the HTTPS URL, identity, logo authorization, and no-commercial-endorsement policy.
