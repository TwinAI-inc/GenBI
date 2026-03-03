"""
Seed billing tables (plans + entitlements) and create PL/pgSQL functions.
Run: python3 seed_plans.py
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from server import create_app
from extensions import db

app = create_app()


def run_sql_file():
    """Run the setup_billing.sql file to create tables, functions, and seed data."""
    sql_path = os.path.join(os.path.dirname(__file__), 'billing', 'setup_billing.sql')

    with app.app_context():
        # Use raw connection to execute the full SQL file as one unit
        # (PL/pgSQL functions contain semicolons so we can't split on ';')
        with open(sql_path, 'r') as f:
            sql = f.read()

        conn = db.engine.raw_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            conn.commit()
            cursor.close()
        except Exception as e:
            conn.rollback()
            print(f'SQL error: {e}')
            raise
        finally:
            conn.close()

        print('\nDone! Verifying...\n')

        # Verify
        rows = db.session.execute(db.text(
            "SELECT p.code, pe.feature_key, pe.limit_value, pe.is_enabled "
            "FROM plans p JOIN plan_entitlements pe ON pe.plan_id = p.id "
            "ORDER BY p.sort_order, pe.feature_key"
        )).fetchall()

        print(f'{"Plan":<12} {"Feature":<20} {"Limit":<10} {"Enabled"}')
        print('-' * 55)
        for row in rows:
            limit = 'Unlimited' if row[2] is None else str(row[2])
            print(f'{row[0]:<12} {row[1]:<20} {limit:<10} {row[3]}')

        plans_count = db.session.execute(db.text("SELECT COUNT(*) FROM plans")).scalar()
        ent_count = db.session.execute(db.text("SELECT COUNT(*) FROM plan_entitlements")).scalar()
        print(f'\nTotal: {plans_count} plans, {ent_count} entitlements')


if __name__ == '__main__':
    print('Seeding billing tables...\n')
    run_sql_file()
    print('\nBilling setup complete!')
