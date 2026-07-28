# Release integrity and verification

Yagami publishes immutable, versioned artifacts through one reviewed GitHub
Actions workflow. A release tag must carry a verified SSH signature, must match
the versions in `pyproject.toml` and `src/yagami/__init__.py`, and must point to
the current tip of the protected `main` branch. Release tags cannot be updated
or deleted while the repository's release-tag ruleset is active.

The release workflow reruns the complete Python, UI, and container CI suite. It
then performs a clean wheel install, imports the installed application, runs the
CLI, starts the built container, checks its health endpoint, and blocks known
fixed HIGH or CRITICAL vulnerabilities. The public outputs are:

- A wheel and source distribution on PyPI and the GitHub release.
- A versioned Helm chart archive on the GitHub release.
- A Helm OCI chart at `oci://ghcr.io/matthewtracy/charts/yagami`.
- Linux `amd64` and `arm64` images at
  `ghcr.io/matthewtracy/yagami:<version>` and an immutable `sha-<commit>` tag.
- Optional lockstep LangChain and LlamaIndex packages on PyPI, the Vercel AI
  SDK provider on npm, and metadata in the official MCP Registry when their
  publisher switches are enabled.
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
gh attestation verify yagami-0.7.2-py3-none-any.whl \
  --repo MatthewTracy/yagami
```

Verify the downloaded Helm chart the same way:

```bash
gh attestation verify yagami-0.7.2.tgz \
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

The optional ecosystem publishers are also credentialless:

- Create the same PyPI Trusted Publisher for `langchain-yagami`,
  `llama-index-llms-yagami`, and `llama-index-embeddings-yagami`, then set
  `ADAPTER_PYPI_PUBLISH_ENABLED=true`.
- Reserve the `@yagami` npm scope and configure npm trusted publishing for
  package `@yagami/ai-sdk-provider` from `release.yml` and environment
  `release`, then set `NPM_PUBLISH_ENABLED=true`.
- Set `MCP_REGISTRY_PUBLISH_ENABLED=true` for GitHub OIDC publication of
  `io.github.MatthewTracy/yagami`; the MCP Registry does not require a stored
  token.

Before enabling automated tag creation, generate a dedicated Ed25519 signing
key, add its public key to the maintainer's GitHub account as an SSH **signing**
key, and store only the private key in the repository Actions secret
`RELEASE_TAG_SIGNING_KEY`. Do not reuse an authentication key. Keep an offline
recovery copy and rotate both the account key and repository secret together.

1. Run the `Prepare release pull request` workflow with the stable version and
   release notes. It updates the core, adapters, npm provider, Helm chart, MCP
   manifest, compatibility manifest, documentation, and changelog in lockstep.
2. Merge the generated pull request through protected `main` after every
   required check passes. Only a merged same-repository `release/<version>`
   pull request can start tag creation; ordinary feature merges cannot create
   or recreate a release tag.
3. The `Create immutable release tag` workflow validates the exact merge
   commit in an unprivileged job. A separate signing job then confirms that
   the commit is still the tip of `main`, creates an SSH-signed tag, verifies
   its signature locally, and pushes it. The tag ruleset prevents later update
   or deletion.
4. The signed tag starts the protected `Release` workflow. Approve the `pypi`
   environment once for the Python publications and the `release` environment
   once for the remaining registries. Downstream smoke tests, promotion, and
   GitHub Release creation inherit those completed gates and do not request
   additional approvals. Approve only after the release-only build,
   clean-install, runtime, vulnerability, SBOM, and attestation steps pass.
5. Do not create or upload release artifacts by hand. Verify the PyPI project,
   GitHub release, GHCR digest, checksums, attestations, package visibility, and
   the tag signature:

   ```bash
   git fetch --tags origin
   git verify-tag v0.7.2
   ```

If a registry outage or partial rerun leaves the immutable artifacts published
but skips `latest` promotion or the GitHub Release, run `Finalize existing
release` with the signed tag, original Release workflow run ID, and published
container digest. The protected workflow verifies the tag, source run,
successful publication and smoke jobs, package and container attestations, and
MCP metadata before it repairs only the missing mutable release state.

Published PyPI filenames and versions cannot be replaced. If a release has a
serious defect, yank it on PyPI, document the reason in the GitHub release,
and publish a new patch version. If credentials or the release workflow may
have been compromised, disable publication, revoke affected credentials,
remove compromised container tags, preserve evidence, and publish a security
advisory before issuing a clean replacement version.
