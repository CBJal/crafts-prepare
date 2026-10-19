# Contributing to Crafts Prepare

Bug reports, documentation improvements and focused pull requests are welcome.

## Report a problem

Use [GitHub Issues](https://github.com/CBJal/crafts-prepare/issues) for ordinary bugs and feature requests. Include the app version, operating system, external tool versions, steps to reproduce, and the expected and actual behavior. Remove private paths and personal information from logs. Use generated test clips or media you have permission to share; do not upload private camera footage, credentials or project files containing sensitive paths.

For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Make a change

1. Fork the repository and create a branch from its current default branch.
2. Follow the development instructions in [README.md](README.md). The native desktop uses PySide6; the optional web interface uses React and Vite.
3. Keep changes focused. Preserve original media, saved-project compatibility, cancellation, output overwrite protections and offline operation. Discuss substantial behavior or dependency changes in an issue first.
4. Run the relevant checks. The backend suite is `python -m pytest -q` from `backend`. For web changes, run `npm run build` and `node --experimental-strip-types --test src/selection.test.ts` from `frontend`. Tests generate synthetic media; some external-tool or platform checks may be skipped and must be reported.
5. Open a pull request describing what changed, how it was tested, limitations, and any setup steps. Include synthetic-media screenshots for UI changes and identify the platform used. Linux checks do not establish Windows installer readiness.

Keep dependencies and their license notices intact. Contributions to the application are offered under its MIT license. Contributions to the optional telemetry helper retain its MIT OR Apache-2.0 licensing. See [LICENSE](LICENSE), the helper's license files and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
