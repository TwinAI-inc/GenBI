"""
GenBI Dashboard Server
Flask backend with Azure OpenAI integration for the Chart Assistant.
Production-hardened with OWASP-aligned security controls.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()  # Load .env before anything reads os.environ

from flask import Flask, request, jsonify, send_from_directory, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_cors import CORS
import json

from extensions import db, migrate
from config import get_config

logger = logging.getLogger(__name__)


# ── App factory ──────────────────────────────────────────────────────────────

def create_app():
    app = Flask(__name__, static_folder='.', static_url_path='')

    # ── Config ────────────────────────────────────────────────────────────
    cfg = get_config()
    app.config.from_object(cfg)
    cfg.init_app(app)

    # ── Extensions ────────────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)

    # CSRF protection (token checked manually on state-changing API routes)
    csrf = CSRFProtect(app)

    # CORS — allow same-origin by default; tighten in production
    cors_raw = os.environ.get('CORS_ORIGINS', '')
    if cors_raw:
        cors_origins = [o.strip() for o in cors_raw.split(',') if o.strip()]
    elif app.debug:
        cors_origins = ['*']
    else:
        cors_origins = []  # No cross-origin in production without explicit config
    CORS(app, resources={r'/api/*': {
        'origins': cors_origins,
        'supports_credentials': bool(cors_origins and cors_origins != ['*']),
    }})

    # Rate limiter — memory for dev, Redis recommended in production
    storage_uri = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri=storage_uri,
    )

    # ── Suppress noisy library loggers in production ──────────────────────
    if not app.debug:
        logging.getLogger('werkzeug').setLevel(logging.WARNING)

    # ── Register blueprints ──────────────────────────────────────────────
    from auth import auth_bp, pages_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)

    from billing import billing_bp, billing_pages_bp
    app.register_blueprint(billing_bp)
    app.register_blueprint(billing_pages_bp)

    # ── Rate limits ──────────────────────────────────────────────────────
    limiter.limit('5/minute')(app.view_functions['auth.login'])
    limiter.limit('5/minute')(app.view_functions['auth.signup'])
    limiter.limit('5/minute')(app.view_functions['auth.forgot_password'])
    limiter.limit('3/hour')(app.view_functions['auth.send_otp'])
    limiter.limit('5/minute')(app.view_functions['auth.verify_otp_endpoint'])
    limiter.limit('10/minute')(app.view_functions['auth.google_auth'])
    limiter.limit('5/minute')(app.view_functions['auth.set_password'])
    limiter.limit('10/minute')(app.view_functions['billing.checkout'])
    limiter.limit('10/minute')(app.view_functions['billing.switch_plan_endpoint'])

    # ── CSRF exemptions (webhook endpoints that receive external POSTs) ──
    # Both /api/billing/webhook and /api/billing/stripe/webhook map to same function
    csrf.exempt(app.view_functions['billing.webhook'])

    # ── CSRF token endpoint ──────────────────────────────────────────────
    @app.route('/api/csrf-token', methods=['GET'])
    @limiter.limit('30/minute')
    def csrf_token():
        return jsonify({'csrf_token': generate_csrf()})

    # ── Health check ─────────────────────────────────────────────────────
    @app.route('/health')
    def health():
        return jsonify({'status': 'ok'}), 200

    # ── Static files ─────────────────────────────────────────────────────
    @app.route('/static/<path:filename>')
    def serve_static(filename):
        return send_from_directory(
            os.path.join(os.path.dirname(__file__), 'static'), filename
        )

    # ── Page routes ──────────────────────────────────────────────────────

    @app.route('/')
    def index():
        return send_from_directory('.', 'index.html', max_age=0)

    @app.route('/dashboard')
    def dashboard_page():
        return send_from_directory('.', 'index.html', max_age=0)

    # ── Session fixation prevention ──────────────────────────────────────
    @app.before_request
    def _regenerate_session_on_login():
        """Clear server-side session on login POST to prevent fixation."""
        if request.path == '/api/auth/login' and request.method == 'POST':
            session.clear()

    # ── Security headers ─────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.sheetjs.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://*.googleusercontent.com; "
            "connect-src 'self' https://accounts.google.com https://oauth2.googleapis.com"
        )
        if not app.debug:
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains'
            )
        return response

    # ── AI auth + billing middleware ──────────────────────────────────────
    @app.before_request
    def _inject_billing_user():
        """Resolve JWT → user on every request; AI endpoints enforce separately."""
        request._billing_user = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            from auth.services.token_service import decode_access_token
            from auth.models import User
            payload = decode_access_token(auth_header[7:])
            if payload:
                user = db.session.get(User, payload.get('sub'))
                if user:
                    request._billing_user = user

    def _require_ai_auth():
        """Return an error response if user is not authenticated, else None."""
        if not request._billing_user:
            return jsonify({'error': 'Authentication required. Please sign in.'}), 401
        return None

    def _check_ai_quota():
        """Check quota for ai_queries. Returns error response or None."""
        user = request._billing_user
        if not user:
            return jsonify({'error': 'Authentication required.'}), 401
        from billing.services.entitlement_service import can_consume, get_user_plan
        check = can_consume(user.id, 'ai_queries', 1)
        if not check['allowed']:
            plan = get_user_plan(user.id)
            return jsonify({
                'error': check['reason'],
                'current_usage': check['current_usage'],
                'limit_value': check['limit_value'],
                'current_plan': plan['plan_code'],
                'upgrade_required': True,
            }), 402
        return None

    def _record_ai_usage():
        user = request._billing_user
        if user:
            from billing.services.entitlement_service import record_usage
            record_usage(user.id, 'ai_queries', 1)

    def _call_ai(prompt, system=None):
        """Call Azure OpenAI and return parsed JSON. Drop-in for old _call_gemini."""
        from services.azure_ai_client import chat_completion_json
        return chat_completion_json(prompt, system=system)

    def _ai_error_response(e):
        """Shared error handler for AI endpoints. Never leaks prompt/response."""
        import uuid as _uuid
        cid = _uuid.uuid4().hex[:12]
        err_str = str(e).lower()
        if 'not configured' in err_str or 'missing' in err_str:
            logger.error('AI not configured cid=%s category=config err=%s', cid, e)
            return jsonify({'error': 'AI service is not configured yet. Contact support.', 'correlation_id': cid}), 503
        if 'authentication' in err_str or 'credential' in err_str:
            logger.error('AI auth error cid=%s category=auth err=%s', cid, e)
            return jsonify({'error': 'AI service authentication error. Contact support.', 'correlation_id': cid}), 503
        if 'timeout' in err_str or 'timed out' in err_str:
            logger.error('AI timeout cid=%s category=timeout', cid)
            return jsonify({'error': 'AI service timed out. Please try again.', 'correlation_id': cid}), 504
        logger.error('AI request failed cid=%s category=unknown err=%s', cid, e)
        return jsonify({'error': 'AI request failed. Please try again.', 'correlation_id': cid}), 500

    # ── AI endpoints ─────────────────────────────────────────────────────

    @app.route('/api/chart-assist', methods=['POST'])
    @limiter.limit('10/minute')
    def chart_assist():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error

        data = request.get_json()
        user_message = data.get('message', '').strip()
        columns = data.get('columns', [])
        sample_rows = data.get('sampleRows', [])
        col_meta = data.get('colMeta', {})

        if not user_message:
            return jsonify({'error': 'No message provided.'}), 400
        if len(user_message) > 2000:
            return jsonify({'error': 'Message too long (max 2000 characters).'}), 400
        if not columns:
            return jsonify({'error': 'No data columns available. Upload a dataset first.'}), 400
        if len(columns) > 200:
            return jsonify({'error': 'Too many columns (max 200).'}), 400

        col_descriptions = []
        for col in columns:
            meta = col_meta.get(col, {})
            desc = col
            if meta.get('isMetric'):
                desc += ' (numeric/metric)'
            elif meta.get('isCategorical'):
                desc += f" (categorical, {meta.get('uniqueCount', '?')} unique values)"
            elif meta.get('isDate'):
                desc += ' (date/time)'
            elif meta.get('isIndex'):
                desc += ' (index/ID)'
            col_descriptions.append(desc)

        sample_str = ""
        if sample_rows:
            sample_str = "\n\nSample data (first 3 rows):\n"
            for row in sample_rows[:3]:
                sample_str += json.dumps(row, default=str) + "\n"

        prompt = f"""You are an expert data analyst creating meaningful, insightful charts. The user has uploaded a dataset with these columns:

{chr(10).join('- ' + d for d in col_descriptions)}
{sample_str}
The user wants: {user_message}

Respond with ONLY a valid JSON object (no markdown, no code fences) with these fields:
- "chartType": one of "bar", "line", "donut", "hbar", "area", "stacked", "gauge", "bubble", "lollipop", "funnel", "waterfall", "radar", "heatmap", "boxplot", "treemap", "candlestick", "sankey"
- "xCol": exact column name for X axis / grouping
- "yCol": exact column name for Y axis / values (null ONLY for donut showing distribution of a single categorical column)
- "aggFn": one of "sum", "avg", "max", "count"
- "groupCol": (optional) categorical column to split into multiple lines — only for line/area/stacked charts
- "sizeCol": (optional) numeric column for bubble size — only for bubble charts
- "width": one of "full", "two-thirds", "half", "one-third" — layout width hint
- "title": short, clean chart title
- "explanation": one sentence explaining the business insight this chart reveals

CHART TYPE SELECTION (choose the type that tells the clearest story):
- "bar": COMPARING values across categories (3-15 unique values). Always pair with numeric yCol.
- "line": TRENDS OVER TIME where xCol is date/time/period. Never for non-sequential categories.
- "donut": PROPORTIONAL BREAKDOWN (2-8 segments). yCol=null for counts.
- "hbar": Horizontal bar — best when category labels are long.
- "area": Like line but emphasizes volume/magnitude over time.
- "stacked": Stacked bars showing composition within each category.
- "gauge": Single KPI arc — use for showing a single metric against a target/threshold. Best for 1 value.
- "bubble": Scatter with sized circles — needs xCol (numeric), yCol (numeric), sizeCol (numeric). Shows 3 dimensions.
- "lollipop": Elegant bar alternative with stem + dot. Same data as bar but cleaner for fewer categories.
- "funnel": Pipeline/conversion stages — data must be in descending order (e.g. Visitors > Leads > Sales).
- "waterfall": Running totals with pos/neg coloring — great for P&L, revenue buildup, bridge charts.
- "radar": Multi-axis comparison on polar coords — best for comparing 4-8 metrics across 2-4 entities.
- "heatmap": Color-intensity matrix — for correlations or time-vs-category patterns. Needs groupCol for Y axis.
- "boxplot": Statistical distribution — shows Q1, median, Q3, whiskers. Good for comparing distributions.
- "treemap": Hierarchical proportional areas — like donut but for more segments (up to 30).
- "candlestick": OHLC financial data — requires open/high/low/close columns with date xCol.
- "sankey": Flow diagram — shows how quantities flow between categories. Needs xCol (source) and groupCol (target).

WIDTH RULES: full for line/area/heatmap/sankey, two-thirds for bar/waterfall/stacked/hbar, one-third for donut/gauge/funnel/radar/treemap, half as default.

AGGREGATION RULES (critical for correctness):
- "sum": Use for additive quantities — revenue, sales, cost, profit, count of items, population, units sold
- "avg": Use for rates, scores, percentages, averages, ratios, temperatures, per-unit metrics — anything where summing is meaningless (e.g. don't sum satisfaction scores or percentages)
- "max": Use only when the user asks for peak/maximum/highest values
- "count": Use when the user wants to know how many rows/records exist per category

LOGICAL PAIRING RULES:
- xCol MUST be categorical or date-based — never use a numeric metric as xCol (don't put "Revenue" on the X axis)
- yCol MUST be numeric/metric — never use a categorical column as yCol
- Never chart an index/ID column — it has no analytical meaning
- If the user asks something vague like "show me a chart", pick the most insightful combination: a categorical column with the fewest unique values (3-10) on X, and the most business-relevant metric on Y
- If multiple metrics could work, prefer revenue/sales/profit over counts, and counts over IDs

MULTI-LINE CHARTS:
- When the user asks for a trend broken down by category (e.g. "revenue trend by product", "sales over time by region"), set groupCol to the categorical column that defines each line. The chart will show one line per unique value.
- groupCol must be categorical with 2-8 unique values — too many lines makes the chart unreadable.
- If no grouping makes sense (single metric over time), omit groupCol or set it to null.
- groupCol is ONLY valid when chartType is "line".

Column names in xCol, yCol, and groupCol MUST exactly match the provided column names (case-sensitive).
If the request is impossible with the available columns, respond with: {{"error": "your explanation"}}"""

        try:
            parsed, _usage = _call_ai(prompt)

            if 'error' in parsed:
                return jsonify({'error': parsed['error']}), 200

            if parsed.get('xCol') and parsed['xCol'] not in columns:
                return jsonify({'error': f"Column '{parsed['xCol']}' not found in your data."}), 200
            if parsed.get('yCol') and parsed['yCol'] not in columns:
                return jsonify({'error': f"Column '{parsed['yCol']}' not found in your data."}), 200
            if parsed.get('groupCol') and parsed['groupCol'] not in columns:
                return jsonify({'error': f"Column '{parsed['groupCol']}' not found in your data."}), 200

            _record_ai_usage()
            return jsonify({
                'chartType': parsed.get('chartType', 'bar'),
                'xCol': parsed.get('xCol'),
                'yCol': parsed.get('yCol'),
                'aggFn': parsed.get('aggFn', 'sum'),
                'groupCol': parsed.get('groupCol'),
                'sizeCol': parsed.get('sizeCol'),
                'width': parsed.get('width', 'half'),
                'title': parsed.get('title', 'Chart'),
                'explanation': parsed.get('explanation', '')
            })

        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response. Please try rephrasing your request.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/chart-modify', methods=['POST'])
    @limiter.limit('10/minute')
    def chart_modify():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error

        data = request.get_json()
        user_message = data.get('message', '').strip()
        current_chart = data.get('currentChart', {})
        columns = data.get('columns', [])
        sample_rows = data.get('sampleRows', [])
        col_meta = data.get('colMeta', {})

        if not user_message:
            return jsonify({'error': 'No message provided.'}), 400
        if len(user_message) > 2000:
            return jsonify({'error': 'Message too long (max 2000 characters).'}), 400
        if not columns:
            return jsonify({'error': 'No data columns available.'}), 400
        if not current_chart:
            return jsonify({'error': 'No current chart provided.'}), 400

        col_descriptions = []
        for col in columns:
            meta = col_meta.get(col, {})
            desc = col
            if meta.get('isMetric'):
                desc += ' (numeric/metric)'
            elif meta.get('isCategorical'):
                desc += f" (categorical, {meta.get('uniqueCount', '?')} unique values)"
            elif meta.get('isDate'):
                desc += ' (date/time)'
            elif meta.get('isIndex'):
                desc += ' (index/ID)'
            col_descriptions.append(desc)

        sample_str = ""
        if sample_rows:
            sample_str = "\n\nSample data (first 3 rows):\n"
            for row in sample_rows[:3]:
                sample_str += json.dumps(row, default=str) + "\n"

        current_spec = json.dumps(current_chart, default=str)

        prompt = f"""You are modifying an EXISTING chart, not creating a new one. The user wants to change something about their current chart.

Current chart specification:
{current_spec}

Available columns:
{chr(10).join('- ' + d for d in col_descriptions)}
{sample_str}
The user wants: {user_message}

IMPORTANT: Keep ALL fields the same UNLESS the user explicitly asks to change them. Return the FULL updated chart spec.

Respond with ONLY a valid JSON object (no markdown, no code fences) with these fields:
- "chartType": one of "bar", "line", "donut", "hbar", "area", "stacked", "gauge", "bubble", "lollipop", "funnel", "waterfall", "radar", "heatmap", "boxplot", "treemap", "candlestick", "sankey"
- "xCol": exact column name for X axis / grouping
- "yCol": exact column name for Y axis / values (null ONLY for donut showing distribution of a single categorical column)
- "aggFn": one of "sum", "avg", "max", "count"
- "groupCol": (optional) categorical column to split into multiple lines — only for line/area/stacked charts
- "sizeCol": (optional) numeric column for bubble size — only for bubble charts
- "width": one of "full", "two-thirds", "half", "one-third"
- "title": short, clean chart title
- "color": one of "cyan", "violet", "emerald", "amber", "rose", "sky", "blue", "indigo", "navy", "slate", "red", "orange", "gold", "lime", "coral", "teal", "mint", "forest", "lavender", "blush", "multi"
- "explanation": one sentence describing what was changed

COLOR RULES: If the user asks to change color, use the closest match from the available colors list above.

CHART TYPE RULES: If the user asks to change chart type, pick the appropriate type from the list above.

COLUMN/AXIS RULES: If the user asks to show a different column, use the exact column name from the available columns list.

If unchanged, carry over the original values exactly. Do not change fields the user did not mention."""

        try:
            parsed, _usage = _call_ai(prompt)

            if 'error' in parsed:
                return jsonify({'error': parsed['error']}), 200

            if parsed.get('xCol') and parsed['xCol'] not in columns:
                return jsonify({'error': f"Column '{parsed['xCol']}' not found in your data."}), 200
            if parsed.get('yCol') and parsed['yCol'] not in columns:
                return jsonify({'error': f"Column '{parsed['yCol']}' not found in your data."}), 200
            if parsed.get('groupCol') and parsed['groupCol'] not in columns:
                return jsonify({'error': f"Column '{parsed['groupCol']}' not found in your data."}), 200

            _record_ai_usage()
            return jsonify({
                'chartType': parsed.get('chartType', current_chart.get('chartType', 'bar')),
                'xCol': parsed.get('xCol', current_chart.get('xCol')),
                'yCol': parsed.get('yCol', current_chart.get('yCol')),
                'aggFn': parsed.get('aggFn', current_chart.get('aggFn', 'sum')),
                'groupCol': parsed.get('groupCol'),
                'sizeCol': parsed.get('sizeCol'),
                'width': parsed.get('width', current_chart.get('width', 'half')),
                'color': parsed.get('color', current_chart.get('color', 'cyan')),
                'title': parsed.get('title', current_chart.get('title', 'Chart')),
                'explanation': parsed.get('explanation', '')
            })

        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response. Please try rephrasing your request.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/dashboard-plan', methods=['POST'])
    @limiter.limit('5/minute')
    def dashboard_plan():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error

        data = request.get_json()
        columns = data.get('columns', [])
        sample_rows = data.get('sampleRows', [])
        col_meta = data.get('colMeta', {})

        if not columns:
            return jsonify({'error': 'No data columns available.'}), 400

        col_descriptions = []
        for col in columns:
            meta = col_meta.get(col, {})
            desc = col
            if meta.get('isMetric'):
                desc += ' (numeric/metric)'
            elif meta.get('isCategorical'):
                desc += f" (categorical, {meta.get('uniqueCount', '?')} unique values)"
            elif meta.get('isDate'):
                desc += ' (date/time)'
            elif meta.get('isIndex'):
                desc += ' (index/ID)'
            col_descriptions.append(desc)

        sample_str = ""
        if sample_rows:
            sample_str = "\n\nSample data (first 3 rows):\n"
            for row in sample_rows[:3]:
                sample_str += json.dumps(row, default=str) + "\n"

        prompt = f"""You are an expert BI dashboard designer. Given this dataset, create a comprehensive dashboard plan with KPI cards and charts.

Columns:
{chr(10).join('- ' + d for d in col_descriptions)}
{sample_str}

Respond with ONLY valid JSON (no markdown):
{{
  "kpis": [
    {{"label": "display name", "col": "exact column name", "aggFn": "sum|avg|max|count", "icon": "dollar-sign|trending-up|users|package|bar-chart|hash|percent|target|zap|activity|database", "format": "currency|percent|number"}}
  ],
  "charts": [
    {{"chartType": "bar|line|donut|hbar|area|stacked|gauge|bubble|lollipop|funnel|waterfall|radar|heatmap|boxplot|treemap|candlestick|sankey",
      "xCol": "column", "yCol": "column", "aggFn": "sum|avg|max|count",
      "groupCol": null, "sizeCol": null,
      "width": "full|two-thirds|half|one-third",
      "priority": 1, "title": "chart title"}}
  ]
}}

RULES:
- Generate 3-5 KPIs: total records first, then top business metrics (revenue > cost > count)
- Generate 3-6 charts, ordered by insight value (most insightful first)
- Use appropriate chart types: line for time trends, bar for comparisons, donut for proportions, gauge for single KPIs
- Width rules: full for line/area, two-thirds for bar/waterfall, one-third for donut/gauge/funnel
- Icon selection: dollar-sign for money, percent for rates, users for people, package for products
- Format: currency for money columns, percent for rate columns, number for everything else
- Column names MUST exactly match the provided names (case-sensitive)
- Never chart index/ID columns"""

        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify({
                'kpis': parsed.get('kpis', []),
                'charts': parsed.get('charts', [])
            })
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/key-influencers', methods=['POST'])
    @limiter.limit('10/minute')
    def key_influencers():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        metric = data.get('metric', '').strip()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        summary = data.get('summary', {})

        if not metric:
            return jsonify({'error': 'No metric selected.'}), 400

        cat_cols = [c for c in columns if col_meta.get(c, {}).get('isCategorical')]
        col_info = []
        for c in cat_cols:
            meta = col_meta.get(c, {})
            vals = meta.get('uniqueVals', [])
            col_info.append(f"- {c}: categorical with {len(vals)} values: {', '.join(str(v) for v in vals[:10])}")

        metric_info = summary.get(metric, {})

        prompt = f"""You are a data analyst performing Key Influencers analysis (like Power BI's Key Influencers visual).

Dataset has {summary.get('rowCount', '?')} rows. Target metric: "{metric}"
Overall average of {metric}: {metric_info.get('avg', '?')}
Overall min: {metric_info.get('min', '?')}, max: {metric_info.get('max', '?')}

Categorical columns and their values:
{chr(10).join(col_info)}

For each categorical column, I've pre-computed the average of "{metric}" grouped by each category value:
{json.dumps(summary.get('groupedAvgs', {}), indent=2, default=str)}

Analyze which categorical columns and their values most influence "{metric}" being HIGH or LOW compared to the overall average.

Respond with ONLY a valid JSON object (no markdown, no code fences) with this structure:
{{
  "influencers": [
    {{
      "factor": "column_name",
      "value": "category_value",
      "direction": "increase" or "decrease",
      "avgWhenTrue": number,
      "overallAvg": number,
      "multiplier": number (ratio of avgWhenTrue / overallAvg, e.g. 1.5 means 50% higher),
      "count": number of rows with this value,
      "explanation": "one sentence explaining the insight"
    }}
  ]
}}

Rules:
- Return at most 10 influencers, sorted by absolute impact (highest multiplier deviation from 1.0 first)
- Include both "increase" and "decrease" influencers
- multiplier > 1.0 means the metric is higher when this factor is present (increase)
- multiplier < 1.0 means the metric is lower when this factor is present (decrease)
- Only include factors where the difference is meaningful (multiplier > 1.15 or < 0.85)
- The "factor" and "value" must exactly match the column names and values provided"""

        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)

        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    # ── New AI endpoints ─────────────────────────────────────────────────

    @app.route('/api/auto-insights', methods=['POST'])
    @limiter.limit('10/minute')
    def auto_insights():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        summary = data.get('summary', {})
        prompt = f"""You are a senior data analyst. Analyze this dataset and provide 3-5 key insights.

Dataset: {len(columns)} columns, {summary.get('rowCount', '?')} rows.
Columns: {json.dumps(columns[:50])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:30]}, default=str)}
Numeric stats: {json.dumps(summary.get('numericStats', {}), default=str)}
Categorical distributions: {json.dumps(summary.get('categoricalDists', {}), default=str)}

Respond with ONLY valid JSON (no markdown):
{{"insights": [{{"text": "insight description", "type": "trend|outlier|correlation|pattern", "severity": "high|medium|low"}}]}}

Rules:
- Focus on actionable business insights
- Include a mix of types (trends, outliers, correlations, patterns)
- Each insight should be 1-2 sentences
- severity=high for critical findings, medium for notable, low for informational"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/anomaly-detect', methods=['POST'])
    @limiter.limit('10/minute')
    def anomaly_detect():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        column_stats = data.get('columnStats', {})
        prompt = f"""You are a data quality expert. Detect anomalies in this dataset.

Columns: {json.dumps(columns[:50])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:30]}, default=str)}
Column statistics (mean, stddev, quartiles): {json.dumps(column_stats, default=str)}

Respond with ONLY valid JSON:
{{"anomalies": [{{"column": "column_name", "description": "what is anomalous", "severity": "high|medium|low"}}]}}

Rules:
- Only flag genuine anomalies (outliers beyond 2 stddev, suspicious distributions, unexpected patterns)
- Return at most 5 anomalies, sorted by severity
- column must exactly match provided column names"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/chart-narrative', methods=['POST'])
    @limiter.limit('10/minute')
    def chart_narrative():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        chart_type = data.get('chartType', '')
        title = data.get('title', '')
        labels = data.get('labels', [])[:20]
        chart_data = data.get('data', [])[:20]
        x_col = data.get('xCol', '')
        y_col = data.get('yCol', '')
        prompt = f"""Write a single concise caption (1-2 sentences) describing what this chart shows, as a data analyst would narrate it.

Chart: {chart_type} chart titled "{title}"
X-axis ({x_col}): {json.dumps(labels, default=str)}
Y-axis ({y_col}): {json.dumps(chart_data, default=str)}

Respond with ONLY valid JSON:
{{"caption": "Your narrative caption here."}}

Rules:
- Mention the key takeaway (highest value, trend direction, notable comparisons)
- Be specific with numbers
- Keep it under 30 words"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/ask-data', methods=['POST'])
    @limiter.limit('10/minute')
    def ask_data():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        question = data.get('question', '').strip()
        if not question:
            return jsonify({'error': 'No question provided.'}), 400
        if len(question) > 2000:
            return jsonify({'error': 'Question too long (max 2000 chars).'}), 400
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        sample_rows = data.get('sampleRows', [])[:5]
        summary = data.get('summary', {})
        context = data.get('context', '')
        prompt = f"""You are a data analyst answering questions about a dataset.

Dataset: {len(columns)} columns, {summary.get('rowCount', '?')} rows.
Columns: {json.dumps(columns[:50])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:30]}, default=str)}
Sample rows: {json.dumps(sample_rows, default=str)}
Numeric stats: {json.dumps(summary.get('numericStats', {}), default=str)}
{('Conversation context: ' + context) if context else ''}

User question: {question}

Respond with ONLY valid JSON:
{{"answer": "Your detailed answer here.", "confidence": "high|medium|low", "chart": {{...}} }}

Rules:
- Answer based on the data provided
- Be specific with numbers when possible
- confidence=high when data clearly supports the answer, medium when inferred, low when speculative
- If the question asks for a filter, also include "filter": {{"column": "col_name", "operator": "equals|contains|gt|lt", "value": "val"}}
- ALWAYS include a "chart" object to visualize the answer. Choose the best chart type:
  - For comparisons/rankings: {{"type": "bar", "labels": ["A","B","C"], "values": [10,20,30], "label": "Metric Name"}}
  - For distributions/proportions: {{"type": "doughnut", "labels": ["A","B","C"], "values": [10,20,30]}}
  - For trends over time: {{"type": "line", "labels": ["Jan","Feb","Mar"], "values": [10,20,30], "label": "Metric Name"}}
  - For single stats/summaries: {{"type": "stats", "items": [{{"label": "Total", "value": "1,234"}}, {{"label": "Average", "value": "56.7"}}, {{"label": "Max", "value": "200"}}]}}
- Use real numbers from the data. Limit labels to 10 items max (show top/bottom entries).
- Only omit chart if the answer is purely textual with no quantifiable data."""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/forecast', methods=['POST'])
    @limiter.limit('10/minute')
    def forecast():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        labels = data.get('labels', [])
        values = data.get('values', [])
        periods = data.get('periods', 3)
        if not values or len(values) < 3:
            return jsonify({'error': 'Need at least 3 data points to forecast.'}), 400
        prompt = f"""You are a forecasting expert. Given this time series data, predict the next {periods} values.

Labels: {json.dumps(labels[-20:], default=str)}
Values: {json.dumps(values[-20:], default=str)}

Respond with ONLY valid JSON:
{{"predicted": [number, ...], "lower": [number, ...], "upper": [number, ...], "explanation": "brief explanation of trend"}}

Rules:
- predicted, lower, upper must each have exactly {periods} values
- lower and upper represent a 80% confidence interval
- Use the trend and seasonality visible in the data
- explanation should be 1 sentence describing the expected trend"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/data-quality', methods=['POST'])
    @limiter.limit('10/minute')
    def data_quality():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        quality_metrics = data.get('qualityMetrics', {})
        prompt = f"""You are a data quality expert. Assess the quality of this dataset.

Columns: {json.dumps(columns[:50])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:30]}, default=str)}
Quality metrics (nulls, type mismatches per column): {json.dumps(quality_metrics, default=str)}

Respond with ONLY valid JSON:
{{"score": 85, "issues": [{{"column": "col_name", "type": "missing|mismatch|outlier|inconsistent", "count": 5, "suggestion": "how to fix"}}]}}

Rules:
- score is 0-100 (100=perfect quality)
- At most 8 issues, sorted by severity
- Provide actionable suggestions for each issue
- column must exactly match provided column names"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/describe-columns', methods=['POST'])
    @limiter.limit('10/minute')
    def describe_columns():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        sample_rows = data.get('sampleRows', [])[:5]
        prompt = f"""You are a data documentation expert. Describe each column in plain English.

Columns: {json.dumps(columns[:50])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:30]}, default=str)}
Sample rows: {json.dumps(sample_rows, default=str)}

Respond with ONLY valid JSON:
{{"descriptions": {{"column_name": "plain English description of what this column represents"}}}}

Rules:
- One description per column
- Each description should be 5-15 words
- Infer meaning from column name, data type, and sample values"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/explain-influencer', methods=['POST'])
    @limiter.limit('10/minute')
    def explain_influencer():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        factor = data.get('factor', '')
        value = data.get('value', '')
        metric = data.get('metric', '')
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        summary = data.get('summary', {})
        prompt = f"""You are a data analyst explaining WHY a factor influences a metric.

Factor: When "{factor}" = "{value}", the metric "{metric}" is significantly different from overall.
Dataset columns: {json.dumps(columns[:30])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:20]}, default=str)}
Summary stats: {json.dumps(summary, default=str)}

Respond with ONLY valid JSON:
{{"explanation": "2-3 sentence explanation of why this factor matters", "compounding_factors": ["other factor 1", "other factor 2"]}}

Rules:
- Explain the causal or correlational relationship
- compounding_factors: list other columns that might interact with this factor (max 3)
- Be specific and data-driven"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/recommendations', methods=['POST'])
    @limiter.limit('10/minute')
    def recommendations():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        influencers = data.get('influencers', [])
        metric = data.get('metric', '')
        direction = data.get('direction', 'increase')
        prompt = f"""You are a business strategy consultant. Based on Key Influencer analysis results, provide actionable recommendations.

Target metric: "{metric}" — goal is to {direction} it.
Key influencers found: {json.dumps(influencers[:10], default=str)}

Respond with ONLY valid JSON:
{{"recommendations": [{{"action": "specific action to take", "impact": "expected impact description", "priority": "high|medium|low"}}]}}

Rules:
- 3-5 recommendations, sorted by priority
- Each action should be specific and implementable
- Impact should reference the metric and expected magnitude
- Focus on the highest-impact influencers"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/suggest-actions', methods=['POST'])
    @limiter.limit('10/minute')
    def suggest_actions():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        has_data = data.get('hasData', False)
        custom_chart_count = data.get('customChartCount', 0)
        prompt = f"""You are a data analytics assistant. Suggest 3-5 smart next actions for the user.

Dataset: {"Loaded with " + str(len(columns)) + " columns" if has_data else "No data loaded yet"}
Columns: {json.dumps(columns[:30]) if has_data else "N/A"}
Column types: {json.dumps({k: 'metric' if v.get('isMetric') else 'categorical' if v.get('isCategorical') else 'date' if v.get('isDate') else 'other' for k, v in list(col_meta.items())[:20]}, default=str) if has_data else "N/A"}
Custom charts created: {custom_chart_count}

Respond with ONLY valid JSON:
{{"suggestions": [{{"text": "short action label", "action_type": "navigate|chart|insight|upload|export"}}]}}

Rules:
- 3-5 suggestions
- action_type determines what happens when clicked:
  - navigate: go to a page (influencers, documents, settings)
  - chart: open chart assistant
  - insight: trigger auto-insights
  - upload: open upload dialog
  - export: trigger export
- Tailor suggestions to current state (e.g. if no data, suggest upload first)"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/chart-explain', methods=['POST'])
    @limiter.limit('10/minute')
    def chart_explain():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        question = data.get('question', '').strip()
        if not question:
            return jsonify({'error': 'No question provided.'}), 400
        if len(question) > 2000:
            return jsonify({'error': 'Question too long (max 2000 chars).'}), 400
        chart_type = data.get('chartType', '')
        title = data.get('title', '')
        labels = data.get('labels', [])[:20]
        chart_data = data.get('data', [])[:20]
        series_names = data.get('seriesNames', [])
        x_col = data.get('xCol', '')
        y_col = data.get('yCol', '')
        agg_fn = data.get('aggFn', '')
        columns = data.get('columns', [])
        prompt = f"""You are a data analyst explaining chart insights. A user is looking at a chart and asking a question about it.

Chart: {chart_type} chart titled "{title}"
X-axis ({x_col}): {json.dumps(labels, default=str)}
Y-axis ({y_col}): {json.dumps(chart_data, default=str)}
{('Series: ' + json.dumps(series_names)) if series_names else ''}
{('Aggregation: ' + agg_fn) if agg_fn else ''}
{('Dataset columns: ' + json.dumps(columns[:30])) if columns else ''}

User question: {question}

Respond with ONLY valid JSON (no markdown, no code fences):
{{"explanation": "2-4 sentence answer to the user's question, referencing specific data points and numbers from the chart.", "highlights": ["key point 1", "key point 2"]}}

Rules:
- Answer specifically about THIS chart's data
- Reference actual numbers and labels from the data provided
- highlights should be 2-4 short bullet points with the most important takeaways
- If the question cannot be answered from the chart data, say so and explain what data would be needed"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    # ── KI Feature Endpoints ─────────────────────────────────────────────

    @app.route('/api/ki-ask', methods=['POST'])
    @limiter.limit('10/minute')
    def ki_ask():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        question = data.get('question', '').strip()
        if not question:
            return jsonify({'error': 'No question provided.'}), 400
        if len(question) > 2000:
            return jsonify({'error': 'Question too long (max 2000 chars).'}), 400
        metric = data.get('metric', '')
        direction = data.get('direction', 'increase')
        influencers = data.get('influencers', [])
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        prompt = f"""You are a data analyst. Given these Key Influencers results for "{metric}" ({direction}), answer the user's question.

Key Influencers found:
{json.dumps(influencers[:10], default=str)}

Dataset columns: {json.dumps(columns[:30])}
Column metadata: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'vals'} for k, v in list(col_meta.items())[:20]}, default=str)}

User question: {question}

Respond with ONLY valid JSON:
{{"answer": "Your detailed answer here.", "highlights": ["key point 1", "key point 2"]}}

Rules:
- Answer based on the influencer analysis data provided
- Be specific with numbers when possible
- highlights should be 2-4 short bullet points with key takeaways"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/ki-interactions', methods=['POST'])
    @limiter.limit('10/minute')
    def ki_interactions():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        metric = data.get('metric', '')
        cross_tab = data.get('crossTabData', [])
        overall_avg = data.get('overallAvg', 0)
        single_factors = data.get('singleFactorInfluencers', [])
        prompt = f"""You are a data analyst. Analyze interaction effects between factor pairs for the metric "{metric}".

Overall average: {overall_avg}
Single factor influencers: {json.dumps(single_factors[:10], default=str)}
Cross-tabulated averages (combined factor pairs): {json.dumps(cross_tab[:20], default=str)}

Identify combinations where the joint effect is notably different from individual effects (synergy or suppression).

Respond with ONLY valid JSON:
{{"interactions": [{{"factor1": "col1", "value1": "val1", "factor2": "col2", "value2": "val2", "combinedAvg": number, "combinedMultiplier": number, "explanation": "description of the interaction", "synergy": "amplifying|suppressing|additive"}}]}}

Rules:
- Return at most 8 interaction pairs, sorted by significance
- synergy: "amplifying" if combined effect > sum of individual, "suppressing" if less, "additive" if roughly equal
- combinedMultiplier = combinedAvg / overallAvg
- Only include interactions with meaningful combined effects"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/ki-segment-compare', methods=['POST'])
    @limiter.limit('10/minute')
    def ki_segment_compare():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        metric = data.get('metric', '')
        segment_col = data.get('segmentColumn', '')
        segment_data = data.get('segmentData', {})
        overall_avg = data.get('overallAvg', 0)
        prompt = f"""You are a data analyst. Compare what drives "{metric}" across segments of "{segment_col}".

Overall average: {overall_avg}
Segment data (grouped averages per segment value):
{json.dumps(segment_data, default=str)}

Identify which factors are shared across all segments vs. unique to specific segments.

Respond with ONLY valid JSON:
{{"segments": [{{"segment": "segment_value", "topDrivers": [{{"factor": "col", "value": "val", "multiplier": number, "direction": "increase|decrease"}}]}}], "shared": [{{"factor": "col", "value": "val"}}], "unique": [{{"segment": "seg_val", "factor": "col", "value": "val"}}]}}

Rules:
- Include top 5 drivers per segment
- shared: factors that appear as top drivers in 2+ segments
- unique: factors that only appear in one segment
- multiplier = segment_avg / overall_avg"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/ki-temporal', methods=['POST'])
    @limiter.limit('10/minute')
    def ki_temporal():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        metric = data.get('metric', '')
        date_col = data.get('dateColumn', '')
        periods_data = data.get('periodsData', {})
        prompt = f"""You are a data analyst. Analyze how key influencers for "{metric}" have changed over time (column: "{date_col}").

Period-by-period influencer analysis:
{json.dumps(periods_data, default=str)}

Identify factors that are strengthening, weakening, or stable over time.

Respond with ONLY valid JSON:
{{"periods": [{{"period": "Q1 2024", "topDrivers": [{{"factor": "col", "value": "val", "multiplier": number, "direction": "increase|decrease"}}]}}], "trends": [{{"factor": "col", "value": "val", "trend": "strengthening|weakening|stable", "explanation": "brief description"}}]}}

Rules:
- Include top 5 drivers per period
- trends: summarize how each major factor changed across periods
- strengthening = multiplier increasing over time, weakening = decreasing, stable = roughly constant"""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/ki-root-cause', methods=['POST'])
    @limiter.limit('10/minute')
    def ki_root_cause():
        auth_err = _require_ai_auth()
        if auth_err:
            return auth_err
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        factor = data.get('factor', '')
        value = data.get('value', '')
        metric = data.get('metric', '')
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        summary = data.get('summary', {})
        mult = data.get('multiplier', '?')
        avg_when = data.get('avgWhenTrue', '?')
        overall = data.get('overallAvg', '?')
        count = data.get('count', '?')
        prompt = f"""You are a data analyst. Why does "{factor}"="{value}" affect "{metric}"?

Data: multiplier={mult}, avgWhenTrue={avg_when}, overallAvg={overall}, count={count}
Columns: {json.dumps(columns[:20])}

Give a 3-level root cause analysis. RESPOND WITH ONLY THIS EXACT JSON STRUCTURE:

{{"chain": [{{"level": 1, "cause": "direct effect in under 15 words"}}, {{"level": 2, "cause": "underlying mechanism in under 15 words"}}, {{"level": 3, "cause": "root cause in under 15 words"}}], "rootAction": "one actionable recommendation sentence"}}

IMPORTANT: Return ONLY the JSON object above. No markdown. No code fences. Each "cause" must be under 15 words."""
        try:
            parsed, _usage = _call_ai(prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    return app


# ── Entry point ──────────────────────────────────────────────────────────────

app = create_app()

if __name__ == '__main__':
    print("\n  GenBI Dashboard running at http://localhost:8000\n")
    app.run(host='0.0.0.0', port=8000, debug=True)
