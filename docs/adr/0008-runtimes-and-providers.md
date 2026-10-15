# ADR-0008: Runtimes and per-stage model providers

Status: accepted, 2026-10-16.

## Decision

The three stages that carry models (`extract`, `ner`, `embed`) each have a
provider setting, and `oss init --runtime cpu|gpu|remote` sets the three
defaults at once. `cpu` is the bundled build and stays the default. `gpu` is
self-contained and generic NVIDIA: the same image plus CUDA builds of the
in-process models, from `deploy/docker/Dockerfile.gpu`. `remote` runs no
local model and takes an HTTP endpoint per stage, with small documented
contracts (an embedding sidecar or OpenAI `/embeddings`, a GLiNER-shaped
`/entities` service, MinerU's jobs API). Any stage can be overridden on its
own, so a site can mix providers.

Three rules keep a switch safe: every chunk records the model that embedded
it and search reads only the current model's chunks; `reindex --stage embed`
re-embeds without replaying the rest; a remote service that is down fails
the stage, with no silent fallback to the CPU model.

## Context

On CPU, named-entity recognition and embedding dominate indexing time on
book-length texts (see the quality numbers). Operators with a GPU, or with
a shared inference stack behind a router, should be able to move exactly
those stages off the CPU without Semascope packaging or depending on any
particular site's services. The product stays agnostic: it ships the
contracts and the generic GPU image; a site's own stack is a `remote`
configuration in that site's repository.

## Consequences

Zero-shot entity providers ask for the active packs' classes, so packs
improve entity recognition without training. The GPU image is amd64 only
and about 15 GB (PyTorch and the CUDA 13 libraries from pip), against 1.4 GB
for the CPU image; slimming it is future work.
MinerU support is implemented against its v1 jobs API and tested with a
mocked server; a live check against a MinerU 4 instance is still owed.

## Alternatives

Bundling a site's inference stack as a compose profile: rejected, it would
tie the open-source product to one deployment. Running GLiNER on CPU as the
default: rejected for now, DeBERTa-large is slower than spaCy on CPU; it may
become an option once measured.
