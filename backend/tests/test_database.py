"""Offline integration checks; never connect to the user's database or brokerage."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sandbox = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(sandbox.name) / 'test.db')
os.environ['USERS_DB_FILE'] = str(Path(sandbox.name) / 'users.json')
os.environ['ACCOUNT_DATA_DIR'] = str(Path(sandbox.name) / 'exports')
os.environ['EXPLORER_PASS'] = 'test-only-password'

from app import app, db
from database.models import Account, Position, Activity, Order
from database.db_sync import sync_export_to_db


def fixture():
    return {'users': [{'username': 'alice', 'accounts': [{
        'account': {'id': 'account-1', 'name': 'Test account'},
        'balances': [{'currency': {'code': 'USD'}, 'cash': 10}],
        'positions': [{'symbol': {'symbol': {'symbol': 'AAPL'}}, 'units': 2, 'price': 100}],
        'orders': [{'id': 'order-1', 'universal_symbol': {'symbol': 'AAPL'}, 'action': 'BUY', 'total_quantity': 2}],
        'activities': [{'id': 'activity-1', 'symbol': {'symbol': 'AAPL'}, 'type': 'SELL', 'units': -1}],
    }]}]}


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.auth = {'Authorization': 'Basic YWRtaW46dGVzdC1vbmx5LXBhc3N3b3Jk'}
        self.export = Path(os.environ['ACCOUNT_DATA_DIR']) / 'snaptrade_full_export.json'
        self.write(fixture())

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def write(self, data):
        self.export.write_text(json.dumps(data))

    def sync(self):
        return sync_export_to_db(str(self.export))

    def test_repeat_sync_and_all_database_endpoints(self):
        for _ in range(2):
            self.assertEqual(self.sync(), dict(accounts=1, positions=1, orders=1, transactions=1))
        self.assertEqual(Account.query.count(), 1)
        self.assertEqual(Position.query.one().market_value, 200)
        self.assertEqual(Activity.query.one().units, -1)
        for table in ('accounts', 'positions', 'orders', 'activities'):
            self.assertEqual(self.client.get('/api/db/' + table).status_code, 401)
            response = self.client.get('/api/db/' + table, headers=self.auth)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['data'][0]['username'], 'alice')
        self.assertEqual(self.client.post('/api/db/sync', headers=self.auth).status_code, 200)

    def test_partial_export_preserves_other_accounts(self):
        self.sync()
        data = fixture()
        data['users'][0]['accounts'][0]['account']['id'] = 'account-2'
        data['users'][0]['accounts'][0]['orders'] = []
        data['users'][0]['accounts'][0]['activities'] = []
        self.write(data)
        self.sync()
        self.assertEqual(Account.query.count(), 2)
        self.assertEqual(Activity.query.count(), 1)

    def test_failed_write_rolls_back_deletion(self):
        self.sync()
        data = fixture()
        data['users'][0]['accounts'][0]['orders'] *= 2
        self.write(data)
        with self.assertRaises(Exception):
            self.sync()
        self.assertEqual(Order.query.count(), 1)
        self.assertEqual(Position.query.count(), 1)

    def test_invalid_export_preserves_data(self):
        self.sync()
        for invalid in ({}, {'users': [{'username': 'alice', 'accounts': [{'account': {'id': 'account-1'}}]}]}):
            self.write(invalid)
            with self.assertRaises(ValueError):
                self.sync()
            self.assertEqual(Position.query.count(), 1)

    def test_legacy_format_and_activity_envelope(self):
        wrapper = fixture()['users'][0]['accounts'][0]
        wrapper['activities'] = {'data': wrapper['activities']}
        self.write({'username': 'legacy', 'accounts': [wrapper]})
        self.sync()
        self.assertEqual(Account.query.one().username, 'legacy')
        self.assertEqual(Activity.query.count(), 1)

    def test_account_ownership_is_preserved(self):
        self.sync()
        data = fixture()
        data['users'][0]['username'] = 'bob'
        self.write(data)
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(Account.query.one().username, 'alice')

    def test_health_checks_database(self):
        response = self.client.get('/api/health')
        self.assertEqual(response.json['database'], 'connected')
        with patch.object(db.session, 'execute', side_effect=RuntimeError('unavailable')), self.assertLogs(app.logger, level='ERROR'):
            self.assertEqual(self.client.get('/api/health').status_code, 503)

    def test_login_migrates_legacy_hash_and_logout(self):
        users = {'alice': {'username': 'alice', 'account_connected': True,
                          'password_hash': hashlib.sha256(b'password').hexdigest()}}
        Path(os.environ['USERS_DB_FILE']).write_text(json.dumps(users))
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'password'}).status_code, 200)
        saved = json.loads(Path(os.environ['USERS_DB_FILE']).read_text())
        self.assertTrue(saved['alice']['password_hash'].startswith('scrypt:'))
        self.assertTrue(self.client.get('/api/auth/status').json['logged_in'])
        self.client.post('/api/auth/logout')
        self.assertFalse(self.client.get('/api/auth/status').json['logged_in'])
        self.assertEqual(self.client.post('/api/auth/complete-setup', json={'username': 'alice'}).status_code, 400)

    def test_setup_requires_registration_session(self):
        users = {'alice': {'username': 'alice', 'snaptrade_user_id': 'id', 'snaptrade_user_secret': 'secret'}}
        Path(os.environ['USERS_DB_FILE']).write_text(json.dumps(users))
        response = self.client.post('/api/auth/complete-setup', json={'username': 'alice'})
        self.assertEqual(response.status_code, 403)

    def test_portfolio_endpoints_with_mock_brokerage(self):
        positions = [{'symbol': {'symbol': {'symbol': 'AAPL'}}, 'units': 2, 'price': 100, 'average_purchase_price': 90}]
        for endpoint in ('summary', 'stocks', 'history'):
            self.assertEqual(self.client.get('/api/portfolio/' + endpoint).status_code, 401)
        with patch('app.get_current_user', return_value={'username': 'alice'}), \
             patch('app.get_portfolio_positions', return_value=positions), \
             patch('app._fetch_previous_closes', return_value={'AAPL': 95}), \
             patch('app._fetch_allocation_metadata', return_value={}), \
             patch('app._get_account_total_balance', return_value=250):
            response = self.client.get('/api/portfolio/summary')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['data']['totalValue'], 200)
            self.assertEqual(response.json['data']['cashBalance'], 50)
            self.assertEqual(self.client.get('/api/portfolio/stocks').json['data'][0]['symbol'], 'AAPL')
            history = self.client.get('/api/portfolio/history?days=7')
            self.assertEqual(len(history.json['data']), 7)

    def test_refresh_reports_success_and_failure(self):
        def run_inline(*, target, **kwargs):
            thread = MagicMock()
            thread.start.side_effect = target
            return thread
        with patch('app.threading.Thread', side_effect=run_inline), \
             patch('app.subprocess.run'):
            self.assertEqual(self.client.post('/api/explorer/refresh', headers=self.auth).status_code, 202)
            self.assertEqual(self.client.get('/api/explorer/refresh', headers=self.auth).json['status'], 'complete')
        with patch('app.threading.Thread', side_effect=run_inline), \
             patch('app.subprocess.run', side_effect=RuntimeError('failed')), \
             self.assertLogs(app.logger, level='ERROR'):
            self.client.post('/api/explorer/refresh', headers=self.auth)
            self.assertEqual(self.client.get('/api/explorer/refresh', headers=self.auth).json['status'], 'failed')

    def test_bad_auth_payloads(self):
        for endpoint in ('login', 'register', 'complete-setup'):
            for payload in ([], {'username': 12}, {'password': []}):
                self.assertEqual(self.client.post('/api/auth/' + endpoint, json=payload).status_code, 400)


if __name__ == '__main__':
    unittest.main()
