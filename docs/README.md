# Documentation index

Start with the root [README](../README.md). The guides in this directory describe the current source and supported component boundaries as reviewed on October 25, 2026. They do not certify current remote health or replay historical deployment receipts as new verification.

| Read when you need to… | Guide |
| --- | --- |
| View README visuals and their provenance | [Assets](assets/README.md) |
| Assess the retained strategy | [Behavior and statistics](strategy-performance.md) |
| Trace project decisions to chats/evidence | [Project sources](project-sources.md) |
| Understand ownership and flow | [Architecture](architecture.md) |
| Install a fresh checkout | [Installation](installation.md) |
| Supply your own credentials | [Configuration](configuration.md) |
| Configure a private server and SSH key | [Private remote configuration](remote-configuration.md) |
| Implement or consume a strategy | [Strategy API](strategy-api.md) |
| Integrate the workstation API | [HTTP API](http-api.md) |
| Develop research modules | [Research](components/research.md) |
| Understand order ownership | [Executor](components/executor.md) |
| Understand perpetual accounts and alerts | [Kalshi perps](components/kalshi-perps.md) |
| Work on UI, jobs or remote observations | [Terminal](components/terminal.md) |
| Reproduce a study | [Data and evidence](data-and-evidence.md) |
| Operate, back up or selectively release | [Operations](operations.md) |
| Run verification | [Testing](testing.md) |
| Prepare a GitHub commit | [Publication](github-publication.md) |
| Resolve installation or runtime problems | [Troubleshooting](troubleshooting.md) |

[Contribution standards](../CONTRIBUTING.md) and [security guidance](../SECURITY.md) apply across packages. [AGENTS.md](../AGENTS.md) describes workspace operating constraints. The [approved local UI/remote operations contract](ui-remote-operations-plan-2026-10-23.md) records the detailed design and preservation requirements.

## Current references versus historical evidence

Package READMEs and dated reports preserve earlier workflows, observations and decisions. A report saying an account “is running” records its authoring date. It does not describe a fresh clone or establish present deployment state. Commands referring to old `outputs/`, `runtime/`, snapshots or private `operations/` receipts require those local artifacts and will not work from source alone.

Generated screenshots, local operations receipts, research datasets, runtime state and most generated output are excluded from upload. Historical documents can intentionally refer to them; those references are provenance pointers rather than downloadable sample data. Do not manufacture replacement evidence to make such a link appear complete.

Keep current onboarding instructions in these guides. Add dated research findings beside the relevant package's research docs, and identify data/cost/selection assumptions instead of appending changing performance claims to the root README.
