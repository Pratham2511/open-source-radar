# Contributing to Open Source Radar

Thanks for helping people find their first open source contribution. Every kind of improvement is welcome.

## Ways to help

Looking for something to pick up? These filters show what's open right now:

- [Good first issues](https://github.com/TanbirRamim/open-source-radar/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22+no%3Aassignee): small, well scoped, a good place to start
- [Help wanted](https://github.com/TanbirRamim/open-source-radar/issues?q=is%3Aopen+is%3Aissue+label%3A%22help+wanted%22+no%3Aassignee): bigger pieces of work
- By area: [guide](https://github.com/TanbirRamim/open-source-radar/labels/guide), [translation](https://github.com/TanbirRamim/open-source-radar/labels/translation), [website](https://github.com/TanbirRamim/open-source-radar/labels/website), [pipeline](https://github.com/TanbirRamim/open-source-radar/labels/pipeline), [data](https://github.com/TanbirRamim/open-source-radar/labels/data)

Kinds of contributions that are welcome:

- **Code.** Improve [`scripts/radar.py`](scripts/radar.py) or the website in [`site/`](site/) (accessibility, mobile, filters). Pure logic changes need a test in [`scripts/test_radar.py`](scripts/test_radar.py), and tests for functions that have none are welcome too.
- **Guides.** Fix mistakes, clarify steps, add a chapter to [`guide/`](guide/README.md), or add a quickstart for a language in [`languages/`](languages/README.md).
- **Translations.** Translate a guide chapter into a language you know well. See [`guide/es/`](guide/es/README.md) and [`guide/hi/`](guide/hi/README.md) for how existing ones are laid out.
- **Data quality.** Add label variants, languages or topic keywords in [`scripts/config.toml`](scripts/config.toml), or report a project whose AI policy, CLA or DCO requirement was detected wrong.
- **Report a stale or wrong listing.** If a listed issue is already taken, closed or not beginner friendly, [open a wrong listing report](https://github.com/TanbirRamim/open-source-radar/issues/new?template=wrong-listing.yml). It takes a minute and saves the next person from a dead end.
- **Star the repository.** If the radar helped you, a star helps other newcomers find it.

Done here and want something different? The same maintainer runs a few smaller projects that also have open good first issues and quick reviews. Ask on an issue there before you start:

- [show-receipts](https://github.com/TanbirRamim/show-receipts/issues?q=is%3Aopen+label%3A%22good+first+issue%22) (Python): a stop hook that checks an AI agent's "all tests pass" claims against the transcript
- [skill-trigger-bench](https://github.com/TanbirRamim/skill-trigger-bench/issues?q=is%3Aopen+label%3A%22good+first+issue%22) (Python): lint and eval for Agent Skills
- [callm](https://github.com/TanbirRamim/callm/issues?q=is%3Aopen+label%3A%22good+first+issue%22) (Python): caching, retries, fallbacks and cost tracking for LLM calls
- [vibereel](https://github.com/TanbirRamim/vibereel/issues?q=is%3Aopen+label%3A%22good+first+issue%22) (TypeScript): turn a Claude Code or Codex session into a short film
- [pr-doorman](https://github.com/TanbirRamim/pr-doorman/issues?q=is%3Aopen+label%3A%22good+first+issue%22) (TypeScript): a GitHub Action that checks new pull requests against plain rules, no AI

## Do not edit generated files

These are rebuilt by the scheduled workflow, and manual edits are overwritten:

- everything in `issues/by-language/` and `issues/by-topic/`, and `issues/README.md`
- `projects/README.md`
- `data/*.json` (and `site/data/` and `site/feeds/`, which are built at deploy time and not committed)
- the block between `RADAR:STATS:START` and `RADAR:STATS:END` in `README.md`

To change what they contain, change `scripts/config.toml` or `scripts/radar.py`.

## Suggesting that a project be excluded

Maintainers who would rather not have their project listed can open an issue, and it will be added to `[exclude]` in `scripts/config.toml`. It disappears from the lists on the next refresh.

## Making a change

1. Pick an issue and comment `/claim` on it. A bot assigns it to you right away, so there's nothing to wait for. Issues that already have an assignee are taken. You can hold 2 issues at a time, `/unclaim` hands one back, and a claim with no pull request or update for 7 days frees up automatically.

   Small fixes (a typo, a broken link, a one-line bug) don't need an issue or a claim at all, just open the pull request.
2. Fork the repository and create a branch.
3. For pipeline changes, install the same Ruff version as CI with `python3 -m pip install ruff==0.17.1`, then run the tests and linter: `python3 -m unittest discover scripts` (Python 3.11+) and `ruff check scripts && ruff format --check scripts`.
4. To try the pipeline on a small scale: `GITHUB_TOKEN=$(gh auth token) python3 scripts/radar.py all --languages "Rust"`, then `python3 -m http.server -d site 8000`. Do not commit the generated data from a partial run.
5. Keep pull requests focused on one change, and describe what changed and why in a few sentences. Put `Closes #123` in the description so the issue closes when it's merged.
6. Draft pull requests are welcome if you want early feedback. Mark it ready for review when you're done.

## What happens after you open a pull request

- CI runs the tests, the linter and a link check. If your GitHub account is brand new, a maintainer has to approve the first run, so the checks may sit at "waiting" for a little while.
- You'll get a review, usually within a day. Reviews here are friendly: expect a few suggestions, and sometimes a maintainer pushes a small follow-up commit to your branch (a test or an edge case) before merging, so pull before you push again.
- Pull requests are squash-merged, and you're credited in the README's Contributors list.
- If two pull requests fix the same issue, the one from the person assigned to the issue is reviewed first. Without an assignee, the first one that's ready and passing gets reviewed first.

## Style

- Plain, direct English. Short sentences. Explain things for someone doing this for the first time.
- Commands in fenced code blocks, and commands that actually work.
- Link to official documentation rather than copying long passages from it.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE), and you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
