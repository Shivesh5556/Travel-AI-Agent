# Validation for v30

Executed in the delivery environment on 21 October 2026:

| Check | Result |
| --- | --- |
| Frontend `npm test` | 15 tests passed, no failures |
| All frontend source/test JS and JSX, actual Babel parser | Passed (23 files at the time of check) |
| Backend `python -m compileall -q backend` | Passed |
| VS Code settings and package/lock JSON parsing | Passed |
| Dependency-light execution of actual planner/recommendation/repair/budget functions | 114 checks passed; 11 fixture-dependent checks skipped |
| Isolated provider/schema/monitor function checks | Location isolation, rejection, group validation, island modes, seasonal month boundary, split horizon and weather baseline checks passed |
| Full backend pytest, including database/auth integration | Not run: pytest/FastAPI/SQLAlchemy could not be installed in this restricted environment |
| Vite production build | Not run successfully: dependency fetch blocked; Vite unavailable |
| Browser, Docker, Render and live provider checks | Not executed |

The isolated checks use the actual pure project functions with unavailable
HTTP/LLM imports isolated. They are not a substitute for the full pytest suite.
New security/database regression tests are included but are not represented as
passing without execution.

Run the complete checks after installing dependencies:

```bash
cd backend
python -m pytest
```

In another terminal from the project root:

```bash
cd frontend
npm ci
npm test
npm run build
```

Use the virtual environment's Python, as shown in `RUN_IN_VSCODE.md`.
