# Launch kit

These are drafts for a maintainer to review and post manually. They are not
published by CI and should not be added to the MkDocs navigation.

## Release gate

Do not use the drafts until all applicable checks are true:

- The target signed release is public on PyPI and GHCR.
- Every package named in a post installs from its stated registry.
- A versioned benchmark report is public, schema-valid, and clearly identifies
  its fixtures, hardware, models, detector settings, limitations, and date.
- The five-minute quickstart has been repeated from a clean environment.
- Documentation links and the demo media work anonymously.
- No unresolved critical/high security finding applies to the promoted path.

Replace every bracketed placeholder and have another person verify the final
claims and links before posting.

