## 0.35.3 (2024-11-22)

### Fix

- avoid closing context multiple times

## 0.35.2 (2024-11-22)

### Fix

- **litestar**: exceptions weren't propagated to contextmanager dependencies

### Refactor

- **litestar**: make after_exception function private
- migrate to uv

## 0.35.1 (2024-09-24)

## 0.35.0 (2024-09-17)

### Feat

- Add `Injected[T]` annotation as a shorthand for `Annotated[T, Inject]` by @nrbnlulu

### Tests

- Add test for `strawberry.subscription` by @nrbnlulu
