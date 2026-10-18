## 1. Retire the tool-grant surface

- [x] 1.1 Remove `GET /api/butlers/{name}/tools` and `PUT /api/butlers/{name}/tools/{tool}` with their models.
- [x] 1.2 Remove the Management tab tool-grant section, its hook, client function and types.
- [x] 1.3 Remove the "Tools & Scope API" requirement and the tool-grant section from the fold-in page structure.

## 2. Verification

- [x] 2.1 A parametrized test asserts both retired routes return 404.
- [x] 2.2 The Management tab test asserts no tool-grant section renders.
- [x] 2.3 Run strict OpenSpec validation and the overwrite and archive guards.
