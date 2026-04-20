"""
Database - SQLite trade tracking (truth layer)
"""

import sqlite3
from typing import Optional, List, Dict
from datetime import datetime
from pathlib import Path

class TradeDatabase:
    def __init__(self, db_path: str = "trades.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT,
                    input_amount REAL,
                    expected_output REAL,
                    actual_output REAL,
                    expected_profit REAL,
                    actual_profit REAL,
                    latency_ms REAL,
                    status TEXT,
                    error TEXT,
                    transaction_id TEXT,
                    priority_fee REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    def log_trade(self, trade: Dict):
        """
        Log a trade result
        
        Args:
            trade: Trade data dictionary
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trades (
                    token, input_amount, expected_output,
                    actual_output, expected_profit,
                    actual_profit, latency_ms, status, error, transaction_id, priority_fee
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get('token'),
                trade.get('input_amount'),
                trade.get('expected_output'),
                trade.get('actual_output'),
                trade.get('expected_profit'),
                trade.get('actual_profit'),
                trade.get('latency_ms'),
                trade.get('status'),
                trade.get('error'),
                trade.get('transaction_id'),
                trade.get('priority_fee')
            ))
            conn.commit()
    
    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent trades"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM trades 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_metrics(self) -> Dict:
        """Calculate performance metrics"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM trades")
            trades = [dict(row) for row in cursor.fetchall()]
        
        total_trades = len(trades)
        if total_trades == 0:
            return {
                'total_trades': 0,
                'successful_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_profit': 0.0
            }
        
        successful = sum(1 for t in trades if t.get('status') == 'success')
        total_pnl = sum(t.get('actual_profit', 0) for t in trades)
        
        return {
            'total_trades': total_trades,
            'successful_trades': successful,
            'win_rate': successful / total_trades,
            'total_pnl': total_pnl,
            'avg_profit': total_pnl / total_trades if total_trades > 0 else 0
        }


def main():
    """Test database"""
    db = TradeDatabase()
    
    # Log a test trade
    db.log_trade({
        'token': 'TEST',
        'input_amount': 100.0,
        'expected_output': 105.0,
        'actual_output': 104.5,
        'expected_profit': 5.0,
        'actual_profit': 4.5,
        'latency_ms': 150,
        'status': 'success',
        'error': None,
        'transaction_id': 'test_tx'
    })
    
    # Get metrics
    metrics = db.get_metrics()
    print("Metrics:", metrics)


if __name__ == "__main__":
    main()
