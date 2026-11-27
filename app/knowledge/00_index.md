# Knowledge base for the analyst agent

Short context notes the agent reads with `read_context(topic)` when a question needs background rather than numbers. Every **number** must still come from a database tool; these notes explain what the numbers mean.

| Topic | File | Use it for |
|---|---|---|
| `use_case` | 01_use_case.md | Who the monitoring is for, what decisions it supports |
| `site` | 02_site.md | The mine and its surroundings (what is known, what is inferred) |
| `facilities` | 03_facilities.md | What each facility class is and how it looks in Sentinel-2 |
| `seasons` | 04_seasons.md | Zambian wet/dry season, fire, dambos: the main confounders |
| `method` | 05_method.md | How change, facilities and indices were derived; caveats |
| `interpretation` | 06_interpretation.md | Playbook: signal → likely cause → how to confirm |
| `business` | 07_business.md | What the results mean for regulators, investors, ESG and the operator |
