# PSX & MUFAP Microservice

**Start with `CONTEXT.md`** — architecture, sources, outputs, schedules, deploy, tests and pitfalls.
Don't re-explore the repo for what it already covers.

**Every change updates `CONTEXT.md` in the same commit:** fix the sections it makes wrong and add a dated
line to its Change log. A change isn't done until the context file says so.

- Working and default branch: `unified-service`. Never commit credentials; env var names only.
- The Fintraxa app consumes this service; if a published path or field changes, update
  `../fintraxa-app/CONTEXT.md` too.
