---
name: handling-rust-errors
description: Use when adding, changing, reviewing, or debugging Rust production business errors, Result types, error conversions, error codes, or HTTP error responses in this repository. Not required for tests, demos, or examples.
---

# Handling Rust Errors

## Rules

- Follow `crates/infra-web/src/resp/mod.rs`: declare business-visible failures with
  `infra_core::define_app_error_codes!`.
- Return `AppResult<T>` from fallible production business code and propagate `AppError`.
- Reuse the owning domain's error enum. New domain/code pairs must be unique and stable;
  never reuse a published code for another meaning.
- Convert third-party errors where their business meaning becomes known.
- Prefer macros ending in `_logged` when an equivalent exists:
  - source `Result` error: `.map_err(map_err_logged!(DomainErr::Variant))?`
  - missing `Option` value: `.ok_or_else(ok_or_logged!(DomainErr::Variant))?`
- Use `err!` or `app_err!` when directly producing an application error.
- Use `AxumResult<T>` and `status_err!` only at HTTP boundaries requiring an explicit
  status candidate.
- Include useful, non-sensitive dynamic context. Do not expose credentials or payloads.

## Standard Pattern

```rust
use infra_core::{define_app_error_codes, map_err_logged, result::AppResult};

define_app_error_codes! {
	IndexerErr("IDX") {
		InvalidBlock = (1001, "Invalid block"),
		StorageWriteFailed = (2001, "Indexer storage write failed"),
	}
}

fn store_block(block: &Block) -> AppResult<()> {
	storage::write(block).map_err(map_err_logged!(
		IndexerErr::StorageWriteFailed,
		format!("height={}", block.height())
	))
}
```

## Avoid

- Crate-local `thiserror` business `Error` enums or custom business `Result<T>` aliases.
- `anyhow::Result`, `anyhow!`, `bail!`, or ad hoc `AppError::new` in public business APIs.
- Propagating third-party errors directly through business APIs.
- `unwrap`, `expect`, or `panic!` for recoverable production failures.

Foreign error types are allowed only when an external trait or protocol requires them;
convert them before entering business code.

## Scope And Review

This pattern is mandatory for production business code. Tests, demos, and examples may
use simpler error handling when it improves readability.

Before editing, use `codegraph` to find the owning error domain and callers, then use
`rust-analyzer` for Rust type and conversion changes. Verify that production failures
have stable typed codes, return `AppResult<T>`, and prefer available `_logged` macros.
