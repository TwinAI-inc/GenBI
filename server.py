"""
GenBI Dashboard Server
Flask backend with Google Gemini AI integration for the Chart Assistant.
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
        return send_from_directory('.', 'index.html')

    @app.route('/dashboard')
    def dashboard_page():
        return send_from_directory('.', 'index.html')

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

    # ── Quota enforcement helper ─────────────────────────────────────────
    def _check_ai_quota():
        # TESTING MODE: quota enforcement disabled — re-enable when done
        return None

    def _record_ai_usage():
        user = getattr(request, '_billing_user', None)
        if user:
            from billing.services.entitlement_service import record_usage
            record_usage(user.id, 'ai_queries', 1)

    def _call_gemini(api_key, prompt):
        """Shared helper: configure Gemini, call, strip markdown fences, parse JSON."""
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(prompt)
        reply_text = response.text.strip()
        if reply_text.startswith('```'):
            reply_text = reply_text.split('\n', 1)[-1]
            if reply_text.endswith('```'):
                reply_text = reply_text[:-3].strip()
            elif '```' in reply_text:
                reply_text = reply_text[:reply_text.rfind('```')].strip()
        return json.loads(reply_text)

    def _ai_error_response(e):
        """Shared error handler for AI endpoints."""
        err_str = str(e).lower()
        if 'api key' in err_str or 'invalid' in err_str or 'authenticate' in err_str:
            return jsonify({'error': 'Invalid API key. Please check your Google API key in Settings.'}), 401
        logger.exception('AI request failed')
        return jsonify({'error': 'AI request failed. Please try again.'}), 500

    # ── AI endpoints ─────────────────────────────────────────────────────

    @app.route('/api/chart-assist', methods=['POST'])
    def chart_assist():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error

        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        user_message = data.get('message', '').strip()
        columns = data.get('columns', [])
        sample_rows = data.get('sampleRows', [])
        col_meta = data.get('colMeta', {})

        if not api_key:
            return jsonify({'error': 'No API key provided. Add your Google API key in Settings.'}), 400
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
- "chartType": one of "bar", "line", "donut"
- "xCol": exact column name for X axis / grouping
- "yCol": exact column name for Y axis / values (null ONLY for donut showing distribution of a single categorical column)
- "aggFn": one of "sum", "avg", "max", "count"
- "groupCol": (optional) categorical column to split into multiple lines — only for line charts (see MULTI-LINE CHARTS below)
- "title": short, clean chart title
- "explanation": one sentence explaining the business insight this chart reveals

CHART TYPE SELECTION (choose the type that tells the clearest story):
- "bar": Use for COMPARING values across categories (e.g. revenue by region, sales by product). Best when xCol is categorical with 3-15 unique values. Always pair with a numeric yCol.
- "line": Use ONLY for TRENDS OVER TIME where xCol is a date/time/period/year/quarter/month column. Never use line charts for non-sequential categories — that creates misleading connections between unrelated points.
- "donut": Use for showing PROPORTIONAL BREAKDOWN of a whole (e.g. market share, distribution of categories). Best with 2-8 segments. Use yCol=null only when showing counts of a categorical column.

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
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content(prompt)

            reply_text = response.text.strip()

            if reply_text.startswith('```'):
                reply_text = reply_text.split('\n', 1)[-1]
                if reply_text.endswith('```'):
                    reply_text = reply_text[:-3].strip()
                elif '```' in reply_text:
                    reply_text = reply_text[:reply_text.rfind('```')].strip()

            parsed = json.loads(reply_text)

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
                'title': parsed.get('title', 'Chart'),
                'explanation': parsed.get('explanation', '')
            })

        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response. Please try rephrasing your request.'}), 200
        except Exception as e:
            err_str = str(e).lower()
            if 'api key' in err_str or 'invalid' in err_str or 'authenticate' in err_str:
                return jsonify({'error': 'Invalid API key. Please check your Google API key in Settings.'}), 401
            logger.exception('Chart-assist request failed')
            return jsonify({'error': 'AI request failed. Please try again.'}), 500

    @app.route('/api/key-influencers', methods=['POST'])
    def key_influencers():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        metric = data.get('metric', '').strip()
        columns = data.get('columns', [])
        col_meta = data.get('colMeta', {})
        summary = data.get('summary', {})

        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content(prompt)

            reply_text = response.text.strip()
            if reply_text.startswith('```'):
                reply_text = reply_text.split('\n', 1)[-1]
                if reply_text.endswith('```'):
                    reply_text = reply_text[:-3].strip()
                elif '```' in reply_text:
                    reply_text = reply_text[:reply_text.rfind('```')].strip()

            parsed = json.loads(reply_text)
            _record_ai_usage()
            return jsonify(parsed)

        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            err_str = str(e).lower()
            if 'api key' in err_str or 'invalid' in err_str or 'authenticate' in err_str:
                return jsonify({'error': 'Invalid API key.'}), 401
            logger.exception('Key-influencers request failed')
            return jsonify({'error': 'AI request failed. Please try again.'}), 500

    # ── New AI endpoints ─────────────────────────────────────────────────

    @app.route('/api/auto-insights', methods=['POST'])
    def auto_insights():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/anomaly-detect', methods=['POST'])
    def anomaly_detect():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/chart-narrative', methods=['POST'])
    def chart_narrative():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/ask-data', methods=['POST'])
    def ask_data():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/forecast', methods=['POST'])
    def forecast():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/data-quality', methods=['POST'])
    def data_quality():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/describe-columns', methods=['POST'])
    def describe_columns():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/explain-influencer', methods=['POST'])
    def explain_influencer():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/recommendations', methods=['POST'])
    def recommendations():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/suggest-actions', methods=['POST'])
    def suggest_actions():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
            _record_ai_usage()
            return jsonify(parsed)
        except json.JSONDecodeError:
            return jsonify({'error': 'AI returned an unexpected response.'}), 200
        except Exception as e:
            return _ai_error_response(e)

    @app.route('/api/chart-explain', methods=['POST'])
    def chart_explain():
        quota_error = _check_ai_quota()
        if quota_error:
            return quota_error
        data = request.get_json()
        api_key = data.get('apiKey', '').strip()
        if not api_key:
            return jsonify({'error': 'No API key provided.'}), 400
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
            parsed = _call_gemini(api_key, prompt)
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
