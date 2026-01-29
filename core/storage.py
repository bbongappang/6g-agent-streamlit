import os
import json
import sqlite3
from datetime import datetime

class Storage:
    def __init__(self, data_dir="data"):
        self.data_dir = data_dir
        self.hot_path = os.path.join(data_dir, "hot_events.jsonl")
        self.cold_path = os.path.join(data_dir, "cold.db")

    def init(self):
        os.makedirs(self.data_dir, exist_ok=True)
        conn = sqlite3.connect(self.cold_path)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT,
          event_json TEXT,
          intent_json TEXT,
          decision_json TEXT,
          eval_json TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS constraints (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT,
          note TEXT,
          constraints_json TEXT
        )
        """)
        conn.commit()
        conn.close()

    def append_hot_event(self, obj: dict, layer: str):
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "layer": layer,
            "payload": obj
        }
        with open(self.hot_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def preview_hot(self, n_lines=30):
        if not os.path.exists(self.hot_path):
            return ""
        with open(self.hot_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n_lines:]
        return "".join(lines)

    def write_cold_record(self, event, intent, decision, eval_json):
        conn = sqlite3.connect(self.cold_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO records(ts,event_json,intent_json,decision_json,eval_json) VALUES(?,?,?,?,?)",
            (
                datetime.utcnow().isoformat() + "Z",
                json.dumps(event, ensure_ascii=False),
                json.dumps(intent, ensure_ascii=False),
                json.dumps(decision, ensure_ascii=False),
                json.dumps(eval_json, ensure_ascii=False),
            )
        )
        conn.commit()
        conn.close()

    def query_recent_records(self, limit=5):
        import pandas as pd
        conn = sqlite3.connect(self.cold_path)
        df = pd.read_sql_query(
            "SELECT id, ts, substr(event_json,1,120) AS event_snip, substr(intent_json,1,120) AS intent_snip, substr(decision_json,1,120) AS decision_snip FROM records ORDER BY id DESC LIMIT ?",
            conn,
            params=(limit,)
        )
        conn.close()
        return df

    def upsert_constraints(self, constraints: dict, note: str = ""):
        conn = sqlite3.connect(self.cold_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO constraints(ts,note,constraints_json) VALUES(?,?,?)",
            (
                datetime.utcnow().isoformat() + "Z",
                note,
                json.dumps(constraints, ensure_ascii=False),
            )
        )
        conn.commit()
        conn.close()
