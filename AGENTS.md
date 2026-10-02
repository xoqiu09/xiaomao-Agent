
## GitHub account and write checks (2026-10-02)

- For personal GitHub pushes, the expected remote is `git@github-personal:xoqiu09/<repo>.git`. Before a push or PR, verify the absolute repository path (`git rev-parse --show-toplevel`), current branch, HEAD, `git status --short`, every fetch/push remote, and the selected push URL. Use `remote.pushDefault` when configured; otherwise inspect `origin`'s push URL. `source-*` and `legacy` remotes are source/history references and are not the default write target.
- Verify the effective Git author and committer identity with `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`; for xoqiu09 writes, confirm they resolve to the intended personal identity before committing. Do not change global Git, SSH, or account configuration as part of a repository task.
- Before personal GitHub API or PR writes, run `/Users/xiuqiu/.local/bin/gh-personal auth status` and `/Users/xiuqiu/.local/bin/gh-personal api user --jq .login`; the latter must return `xoqiu09`. Do not infer CLI/API identity from SSH success.
- Open a personal PR only with `/Users/xiuqiu/.local/bin/gh-personal pr create --repo xoqiu09/<repo>` (and pass `--repo xoqiu09/<repo>` to every other personal-repository PR command). The ordinary `gh` CLI is associated with `qianqiu0404`; treat its token as user-reported expired and do not attempt to log in or repair it.
