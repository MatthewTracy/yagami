# Release integrity and verification

Yagami publishes immutable, versioned artifacts through one reviewed GitHub
Actions workflow. A release tag must be annotated, must match the versions in
`pyproject.toml` and `src/yagami/__init__.py`, and must point to the current tip
of the protected `main` branch. Release tags cannot be updated or deleted while
the repository's release-tag ruleset is active.

The release workflow reruns the complete Python, UI, and container CI suite. It
then performs a clean wheel install, imports the installed application, runs the
CLI, starts the built container, checks its health endpoint, and blocks known
fixed HIGH or CRITICAL vulnerabilities. The public outputs are:

- A wheel and source distribution on PyPI and the GitHub release.
- Linux `amd64` and `arm64` images at
  `ghcr.io/matthewtracy/yagami:<version>` and an immutable `sha-<commit>` tag.
- SHA-256 checksums, an SPDX Python-environment SBOM, a Python license
  inventory, and the exact container digest on the GitHub release.
- GitHub/Sigstore build-provenance attestations for both Python distributions
  and the pushed container digest. BuildKit also publishes registry-native
  provenance and SBOM attestations with the image.

Attestations establish which repository, workflow, commit, and environment
built an artifact. They do not prove that the code is vulnerability-free or
appropriate for a particular regulated workload.

## Verify a release

Download the release assets, then verify their checksums:

```bash
sha256sum --check SHA256SUMS
```

Verify a wheel or source archive against this repository's GitHub attestation:

```bash
gh attestation verify yagami-0.4.1-py3-none-any.whl \
  --repo MatthewTracy/yagami
```

Read `release-metadata/container-digest.txt` from the release and verify that
exact OCI subject:

```bash
gh attestation verify \
  oci://ghcr.io/matthewtracy/yagami@sha256:<digest> \
  --repo MatthewTracy/yagami
```

Production deployments should use the verified digest, not a mutable local
alias:

```bash
docker pull ghcr.io/matthewtracy/yagami@sha256:<digest>
```

## Maintainer release procedure

PyPI uses Trusted Publishing. For the first release, create a pending publisher
for project `yagami` with owner `MatthewTracy`, repository `yagami`, workflow
`release.yml`, and environment `pypi`. After it is registered, set the GitHub
repository variable `PYPI_PUBLISH_ENABLED=true`. Never add a PyPI API token or
password to GitHub secrets.

1. Update the stable version in `pyproject.toml` and
   `src/yagami/__init__.py`, and move the shipped changelog entries from
   `Unreleased` into that version. Run `uv lock` and regenerate
   `requirements.container.lock` with the command recorded at the top of that
   file whenever runtime dependencies change. Regenerate
   `requirements.build.lock` from `requirements.build.in` when build tooling
   changes.
2. Merge the change through the protected branch after every required check
   passes.
3. Create and push an annotated tag from the exact `origin/main` commit:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a v0.4.1 -m "Yagami 0.4.1"
   git push origin v0.4.1
   ```

4. Do not create or upload release artifacts by hand. Wait for the `Release`
   workflow and verify the PyPI project, GitHub release, GHCR digest,
   checksums, attestations, and package visibility.

Published PyPI filenames and versions cannot be replaced. If a release has a
serious defect, yank it on PyPI, document the reason in the GitHub release,
and publish a new patch version. If credentials or the release workflow may
have been compromised, disable publication, revoke affected credentials,
remove compromised container tags, preserve evidence, and publish a security
advisory before issuing a clean replacement version.
