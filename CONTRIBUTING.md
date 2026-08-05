# Contributing

Keep changes focused, typed, and efficient enough for a Raspberry Pi touchscreen.

- Preserve the named domain structures in `lib/types.ts`.
- Keep status probes isolated in `lib/status/` and cache expensive work.
- Maintain touch scrolling, accessible labels, hidden scrollbars, and the 800×480 layout.
- Never commit `.env`, runtime databases, credentials, private addresses, or operator notes.
- Treat the Time Machine source as read-only and preserve every active-write guard.
- Run `npm run lint` and `npm run build` before submitting a change.

Use generic example hosts, users, labels, and paths in code and documentation.
