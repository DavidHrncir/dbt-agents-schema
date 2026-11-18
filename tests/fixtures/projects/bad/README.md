# `bad` fixture

Triggers every check rule in SPEC section 8. Some rules exclude each other in a single
run, so they are reached through options:

| Rule | How |
| --- | --- |
| `config_missing` | `--config` pointing at a path that does not exist |
| `config_invalid` | `--config agents_schema.invalid.yml` (schema) or `agents_schema.unparseable.yml` (YAML) |
| `manifest_missing` | no `--manifest`: this project has no `target/manifest.json` |
| `project_key_invalid` | default project from the over-long dbt project name |
| `skill_key_duplicate` | `reports/daily.md` and `Reports\Daily.md` both map to `skill/<project>/reports/daily` (case-insensitive, `\` treated as a separator) |
| `skill_frontmatter_invalid` | `broken_frontmatter.md` |
| `skill_without_uses` | `no_uses.md` |
| `skill_unresolved_uses` | `unresolved.md` |
| `missing_model_description` | `orders` (public, no description) |
| `missing_column_description` | `orders.order_id` |
| `sampled_values_in_description` | `customers` and its columns |

All other rules use `--manifest tests/fixtures/manifests/bad.json` with the default config.
