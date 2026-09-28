# Keep A Grip

[Keep A Grip](https://keepagrip.vercel.app) is a catalogue of problems in ROS 2, robotics,
perception, AI, foundation models, simulation and embedded work. You solve one with whatever
assistant you like, then grip quizzes you on the diff you produced and reports the score.
The score measures you, not the model.

## The loop

```sh
grip login kag_…                     # a token from your dashboard
grip problems --category ros2        # what is there
grip solve cmd-vel-safety-filter     # sets up ./cmd-vel-safety-filter with PROBLEM.md
# solve it with Claude Code, Codex, Cursor, or by hand; commit as you go
grip submit                          # runs the tests, asks five questions, reports the score
```

`grip submit` diffs everything you did since `grip solve` set the repository up, committed
or not, and hands the problem author's focus areas to the quiz generator as context. The quiz
itself runs on your machine with your configured provider. Keep A Grip receives the score,
the per-question focus and points, the provider and model names, the diff size and whether
the tests passed. Never the diff, the questions or your answers.

## Commands

| Command | What it does |
| --- | --- |
| `grip login TOKEN [--url URL]` | Verify the token and store it under your data home, readable by you only. |
| `grip logout` | Forget the stored token. |
| `grip whoami` | Which account the stored token belongs to. |
| `grip problems [--category SLUG]` | List problems you can solve. |
| `grip solve SLUG [--dir PATH]` | Clone the starter repo, or start an empty one, write `PROBLEM.md` and `.grip.toml`, commit, remember the base commit. |
| `grip submit [--no-tests] [--dry-run]` | Run the problem's test command, quiz you on the diff, report the attempt. `--dry-run` prints the payload instead. |

`grip submit` takes the usual quiz options too: `--provider`, `--model`, `--difficulty`,
`--report`. The pass mark comes from the problem through `.grip.toml`; a flag still wins.

## Where things live

- Token: `$XDG_DATA_HOME/grip/credentials.json` (or `$GRIP_DATA_HOME`). `KEEPAGRIP_TOKEN`
  and `KEEPAGRIP_URL` in the environment override it, which is handy in CI or on a shared
  machine.
- Solve state: `.git/grip/keepagrip.json` inside the solve repository. Delete it and the
  repository is a plain repository again.
- Every graded attempt is also a normal grip report in `.git/grip/last-report.json` and
  `history.jsonl`, so `grip study export` sees it.

## Writing a problem

Anyone signed in can add a problem from the site. The best ones have a real situation, a
concrete definition of done, a test command, and a short list of focus areas: what a good
reviewer would probe once an assistant has written the code. Those focus areas are exactly
what the quiz leans on.
