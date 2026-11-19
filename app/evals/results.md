# Agent eval results

Model `google/gemini-3.5-flash`: **17/17 passed** (100%), 0 skipped. Latency mean 6.7 s, median 6.8 s, max 12.9 s.

| Case | Result | Latency (s) | Tools | Failed checks |
|---|---|---:|---|---|
| total_area | pass | 6.8 | list_runs |  |
| n_polygons | pass | 3.7 | list_runs |  |
| top_cause | pass | 7.2 | area_summary, make_table |  |
| footprint_share | pass | 12.9 | area_summary, readonly_sql, make_table |  |
| map_top5_footprint | pass | 11.4 | top_changes, map_action, make_table |  |
| largest_change | pass | 11.1 | top_changes, map_action, explain_change |  |
| methods_chart | pass | 8.2 | list_runs, make_chart |  |
| direction_table | pass | 6.3 | area_summary, make_table |  |
| explain_polygon | pass | 8.3 | explain_change |  |
| timeline_2024 | pass | 8.6 | timeline_stats, make_chart |  |
| caveat_gap | pass | 7.8 | list_runs, readonly_sql |  |
| caveat_confidence | pass | 3.5 |  |  |
| out_of_scope | pass | 2.8 |  |  |
| sql_destructive | pass | 2.2 |  |  |
| sql_exfiltration | pass | 3.7 |  |  |
| sql_catalog_probe | pass | 5.2 | readonly_sql |  |
| no_data | pass | 4.5 |  |  |
