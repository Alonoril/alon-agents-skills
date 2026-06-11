---
name: handling-rust-errors
description: Use when adding, changing, reviewing, or debugging Rust business errors, Result return types, error conversions, error codes, or HTTP error responses in this repository.
---

# Handling Rust Errors

## Core Rule

Represent every business-visible failure with a stable typed error code declared by
`infra_core::define_app_error_codes!`, following `crates/infra-web/src/resp/mod.rs`.
Propagate it as `AppError` through `AppResult<T>`.

Do not introduce a crate-local `thiserror` business `Error` enum or custom `Result<T>`
alias when the failure can use the application error contract.

## Required Workflow

1. Run `codegraph` first to locate the owning module, existing error-code enum, callers,
   and response boundary.
2. Run `rust-analyzer` before changing Rust types, conversions, or return signatures.
3. Reuse the owning domain's error-code enum. If none exists, define one near the
   domain's error boundary with a unique short domain string and stable numeric codes.
4. Return `AppResult<T>` from fallible business and infrastructure functions.
5. Convert source errors to `AppError` at the boundary where their business meaning is
   known. Add only useful, non-sensitive dynamic context.
6. At HTTP boundaries, use `AxumResult<T>` and `status_err!` only when an explicit HTTP
   status candidate is required.
7. Add focused tests for the domain, code, static message, conversion, and extended
   message when introducing or changing an error code.

## Standard Definition

```rust
use infra_core::define_app_error_codes;

define_app_error_codes! {
	IndexerErr("IDX") {
		InvalidBlock = (1001, "Invalid block"),
		StorageWriteFailed = (2001, "Indexer storage write failed"),
	}
}
```

Each domain/code pair is a stable machine-readable contract. Never renumber or reuse a
published code for a different meaning. Keep the static message concise and stable.

## Quick Reference

| Situation | Required pattern |
|---|---|
| Return a typed business failure | `err!(IndexerErr::InvalidBlock)` |
| Return with useful context | `err!(IndexerErr::InvalidBlock, format_args!("height={height}"))` |
| Construct an `AppError` | `app_err!(IndexerErr::InvalidBlock)` |
| Map a source error | `.map_err(map_err!(IndexerErr::StorageWriteFailed))?` |
| Map and expose source error in error log | `.map_err(map_err_logged!(IndexerErr::StorageWriteFailed))?` |
| Convert `Option::None` lazily | `.ok_or_else(ok_or_logged!(IndexerErr::InvalidBlock))?` |
| Preserve a static message without logging | `AppError::from_code(IndexerErr::InvalidBlock)` |
| Attach dynamic context without logging | `AppError::from_code_msg(IndexerErr::InvalidBlock, context)` |
| Fallible business function | `fn operation(...) -> AppResult<T>` |
| HTTP handler with explicit status | `fn handler(...) -> AxumResult<T>` plus `status_err!` |

Prefer `map_err!` for normal source-error conversion: it logs the stable code at error
level and source details at debug level. Use `map_err_logged!` only when source details
must be visible at error level.

## Complete Example

```rust
use infra_core::{
	define_app_error_codes, map_err,
	result::AppResult,
};

define_app_error_codes! {
	AccountErr("ACT") {
		AccountDecodeFailed = (1001, "Account decode failed"),
	}
}

fn decode_account(bytes: &[u8]) -> AppResult<Account> {
	bincode::decode_from_slice(bytes, bincode::config::standard())
		.map(|(account, _)| account)
		.map_err(map_err!(
			AccountErr::AccountDecodeFailed,
			format!("payload_len={}", bytes.len())
		))
}
```

## Prohibited Patterns

- Do not use `anyhow::Result` as a business or public API return type.
- Do not create ad hoc string-only errors with `anyhow!`, `bail!`, or
  `AppError::new` when a typed code can represent the failure.
- Do not define business errors only with `#[derive(thiserror::Error)]`.
- Do not propagate third-party errors directly through business APIs.
- Do not use `unwrap`, `expect`, or `panic!` for recoverable runtime failures.
- Do not leak credentials, payload contents, or other sensitive values in dynamic
  messages.
- Do not allocate dynamic messages on successful paths; use lazy conversion helpers.

Low-level protocol adapters may keep a foreign error type only when a required external
trait fixes that type. Convert it to a typed application error before it enters business
code.

## Review Checklist

- Every business failure maps to a `define_app_error_codes!` variant.
- Domain strings and numeric codes are unique, stable, and owned by the relevant module.
- Public fallible business APIs return `AppResult<T>` or the appropriate response-boundary
  result.
- Source errors are converted once at the boundary with useful context.
- Logs contain `domain_code`; sensitive source details are not exposed to clients.
- Tests assert the stable domain/code contract and conversion behavior.
