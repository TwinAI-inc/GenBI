"""
GenBI Chart & Dashboard Best Practices Knowledge Base

Distilled from:
  - "Which Chart or Graph Is Right for You?" (Tableau)
  - "10 Best Practices for Building Effective Dashboards" (Tableau)

Used as RAG context in LLM prompts for chart selection, dashboard planning,
and recommendation endpoints.
"""

# ── Chart Selection Guide ────────────────────────────────────────────────────
# When to use each chart type, with tips for maximum impact.

CHART_SELECTION_GUIDE = """
CHART SELECTION BEST PRACTICES (expert knowledge):

BAR CHART: Best for comparing data across categories, highlighting differences, showing trends/outliers.
  - Ideal when data splits into multiple categories (e.g., sales by department, volume by size)
  - Tips: add color for quick visual identification; use stacked/side-by-side for deeper breakdown;
    combine with maps for drill-down; plot positive and negative on same axis to show contrasts.

LINE CHART: Best for viewing trends over time or continuous evolution of values.
  - Use for stock prices, page views, any time-series data. Not limited to time — any ordinal dimension works.
  - Tips: combine with bar charts for dual-axis context; shade area under lines for quantity cues;
    use multiple colored lines for relative contribution comparison.

PIE/DONUT CHART: Best as supplementary detail alongside other charts, NOT as primary focus.
  - Alone, pie charts make it hard to compare proportions accurately.
  - Tips: limit to ≤6 wedges; use alongside other charts for drill-down context;
    overlay on maps for geographic breakdown.

MAPS: Essential for any location-based data (postal codes, states, countries, custom geocoding).
  - Tips: use maps as filters for other charts — intuitive drill-down;
    layer data points on maps for precision; vary mark size for additional dimension.

SCATTER PLOT: Best for investigating relationships between variables and identifying predictors.
  - Tips: use cluster analysis to identify segments; add highlight actions to find common attributes;
    customize marks to distinguish groups; only use when meaningful correlation exists (r > 0.3).

HISTOGRAM: Best for showing data distribution across bins/groups.
  - Tips: test different bin sizes to find most useful grouping; add color to break bins by a second category.

TREEMAP: Best for showing parts-to-whole relationships using nested rectangles.
  - Efficient use of space for percent-of-total per category.
  - Tips: color rectangles by category for easier distinction; combine with bar charts for comparison + breakdown.

BUBBLE CHART: Best for showing relationships between 3+ measures (size + color + position).
  - Tips: add color for extra dimension; overlay on maps for geographic context.

BOX-AND-WHISKER PLOT: Best for showing distributions — median, quartiles, and outliers.
  - Tips: hide points within the box to focus on outliers; compare across categorical dimensions.

BULLET CHART: Best for comparing progress against a goal — replaces gauges/meters.
  - Shows more information in less space than traditional gauge.
  - Tips: use color for achievement thresholds; add to dashboards for summary KPI insights.

GANTT CHART: Best for project schedules, duration data, and activity timelines.
  - Tips: add color to bars for key variable identification; combine with maps and other charts.

HIGHLIGHT TABLE: Best for combining color cues with precise figures — enhanced heat map.
  - Tips: combine with line charts to keep trends visible while drilling into cross-sections.
"""

# ── Dashboard Design Best Practices ──────────────────────────────────────────
# 10 rules for building effective dashboards.

DASHBOARD_BEST_PRACTICES = """
DASHBOARD DESIGN BEST PRACTICES (expert knowledge):

1. KNOW YOUR AUDIENCE: Design for the specific viewer — busy executives need quick KPIs (15 seconds),
   analysts need detailed drill-down views. Match complexity to expertise level.
   Beginner audiences need action-oriented labels; advanced users can handle more density.

2. CONSIDER DISPLAY SIZE: Design responsive dashboards. Mobile users need simplified views
   with only the most important KPIs. Stack content vertically for phone screens.
   Limit interactivity on small screens — no more than 3 interactions.

3. PLAN FOR FAST LOAD TIMES: Pre-aggregate data, use extracts, push calculations to the database.
   Even beautiful dashboards fail if they're slow.

4. LEVERAGE THE SWEET SPOT: Place the most important view in the UPPER-LEFT corner.
   Viewers scan dashboards like web pages — top-left first. Use shading, lines, white space,
   and color to group logically related elements.

5. LIMIT VIEWS AND COLORS: Stick to 2-3 main views max. Too many views sacrifice the big picture.
   Use color intentionally — too many colors create visual overload. Prefer muted, modern palettes
   over harsh saturated colors. Inconsistent shading makes it harder to see relationships.

6. ADD INTERACTIVITY: Use one view as a filter for others. Enable highlight actions so selections
   in one chart highlight related data in others. Show filters as checkboxes, radio buttons,
   or dropdowns with clear instructions.

7. FORMAT LARGEST TO SMALLEST: Apply formatting in order: Theme → Workbook → Worksheet.
   This prevents accidentally overwriting changes and keeps consistency.

8. LEVERAGE TOOLTIPS: Tooltips are "the story within your story." Put the most important info
   at the top of the tooltip. Use Viz-in-Tooltip to add context without cluttering the main view.
   Tooltips should reinforce the dashboard's narrative.

9. ELIMINATE CLUTTER: Every element must serve a purpose. Remove unnecessary titles, legends,
   axis labels. Use white space and floating layouts. Simple, clean design reveals hidden insights
   because viewers aren't sifting through noise.

10. TEST FOR USABILITY: After building a prototype, ask your audience how they use it.
    Are they ignoring certain views? Creating their own versions? Use feedback to iterate.
"""

# ── Combined Knowledge Context for LLM Prompts ──────────────────────────────

CHART_KNOWLEDGE_CONTEXT = CHART_SELECTION_GUIDE + "\n" + DASHBOARD_BEST_PRACTICES
