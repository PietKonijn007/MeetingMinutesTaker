# Contributing to MeetingMinutesTaker

Thanks for your interest in improving MeetingMinutesTaker.

## License of contributions

This project is released under the **GNU Affero General Public License v3.0** (see [LICENSE](LICENSE)) and is additionally offered under separate commercial license terms (see [COMMERCIAL.md](COMMERCIAL.md)).

To support this dual-licensing model, all contributors must agree to the **[Individual Contributor License Agreement (ICLA)](CLA.md)** before a contribution can be merged. You keep your copyright; the ICLA grants the Project Owner the right to distribute your contribution under AGPL-3.0 and under commercial license terms.

## How to contribute

1. **Fork** the repository and create a branch from `main`.
2. Make your change. Keep PRs focused — one topic per PR.
3. If your change is user-visible, update `README.md` and any affected docs in `specs/` or `docs/`.
4. Add or update tests. Run the test suite locally before opening a PR.
5. **Open a pull request** against `main`. Fill out the PR description with what and why.

## Signing off your commits (CLA acceptance)

Every commit in your pull request must include a DCO/CLA sign-off line:

```
Signed-off-by: Your Name <your.email@example.com>
I agree to the MeetingMinutesTaker ICLA v1.0.
```

Add this automatically with:

```bash
git commit -s -m "your message"
```

(the `-s` adds `Signed-off-by`; add the ICLA-acceptance line manually the first time, or set up a commit template).

For your **first** contribution, please also leave a comment on the pull request explicitly stating:

> _I, \<your name\>, have read and agree to the MeetingMinutesTaker ICLA v1.0 ([CLA.md](CLA.md))._

If you are contributing on behalf of your employer or another legal entity, we need a **Corporate CLA** in place first — contact the author (hofkensjurgen+meetingminutes@gmail.com) to arrange one before submitting.

## What we're looking for

- Bug fixes with a regression test
- Pipeline improvements (transcription accuracy, summarization quality, template improvements)
- New meeting-type templates (see [templates/](templates/))
- Performance improvements
- Documentation improvements

## What requires discussion first

Open an issue before starting work on:

- New external dependencies
- Changes to the public CLI or REST API surface
- Changes to the database schema
- Security-sensitive code paths

## Code style

- Python: type hints required on public functions, keep functions small, prefer dataclasses/pydantic over loose dicts.
- Favour editing existing files over creating new ones. No comments explaining obvious code.
- Don't add backwards-compatibility shims for hypothetical future changes.

## Questions

Open an issue or email the author at hofkensjurgen+meetingminutes@gmail.com.
