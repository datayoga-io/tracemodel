# Contributing to tracemodel

Welcome! We are glad you want to contribute. This guide covers the setup, the checks, how to add a feature, and the
rules about licensing that every contribution follows.

## Development setup

We use [uv](https://docs.astral.sh/uv/). Python 3.12 and 3.13 are supported.

```bash
uv sync                      # install the package and the dev tools
uv run pytest -q             # run the tests
uvx ruff check src tests     # lint
```

These are the same two checks CI runs, on both Python versions (`.github/workflows/ci.yml`). Run them before you open
a PR.

## Adding a feature or a fix

1. **One change per PR.** A feature, a fix, or a docs change, not a mix. Small PRs get reviewed faster.
2. **Tests first.** Add a test that fails without your change. A bug fix includes the test that would have caught it.
3. **Update the README** for any public call you add or change, in the method table or the section that covers it.
4. **Add a CHANGELOG entry** under "Unreleased". Say what a user can now do, in a sentence or two. Mark a breaking
   change with **Breaking:**.
5. **Open the PR** against `main` and link the issue it closes (`Closes #31`). Say what changed and why.

If you are not sure a change fits, open an issue first and ask.

## Writing style

The README, the docs and the docstrings follow one style:

- Short sentences, in the active voice.
- One term for one meaning. If the docs say "line item", do not say "row" for the same thing elsewhere.
- Examples use made-up data. Do not put real customer, employer or personal data in tests, docs or examples, and keep
  examples neutral about currency.

## Licensing and sign-off

tracemodel is licensed under the [Apache License 2.0](LICENSE). Every contribution is accepted on these terms.

**Your contribution is under Apache 2.0.** As section 5 of the license says, anything you submit for inclusion in the
project is under the same license as the project, including its patent grant (section 3).

**DataYoga may use it commercially.** By contributing, you also agree that DataYoga, the project's maintainer, may use
your contribution in the project and in its other offerings, including a hosted service or a commercial edition, and
may distribute it under other license terms. You keep your own copyright. This keeps the project free to grow without
asking every contributor again.

**Sign off every commit** with `git commit -s`. It adds a line to the commit message:

```
Signed-off-by: Your Name <you@example.com>
```

Use your real name and an address where you can be reached. The line says you agree to the
[Developer Certificate of Origin](https://developercertificate.org/). In short:

- you wrote the contribution, or you have the right to submit it under the project's license;
- you understand that the contribution and your sign-off are public, and are kept with the project.

If you forgot, fix the last commit with `git commit --amend -s`, or every commit in the PR with
`git rebase --signoff main`, then force-push.

**Be careful with code that is not yours.**

- Do not copy code from a project with an incompatible license (GPL and similar). Check the license first.
- If your employer may own your work, get their permission before you contribute.
- Code written with an AI assistant is still your contribution. Review it, and sign off only if you are sure you may
  submit it.
- A new dependency needs a permissive license (MIT, BSD, Apache 2.0 or similar). The core has one dependency today and
  the rest are optional extras, so please discuss a new one in an issue first.

You do not need to add a copyright header to files. The project's git history records who wrote what.

## Releases

A maintainer publishes a release. Pushing a tag that starts with `v` (for example `v0.3.0`) runs
`.github/workflows/release.yml`: it runs the tests, checks that the tag matches the `version` in `pyproject.toml`,
builds the package and publishes it to PyPI.
