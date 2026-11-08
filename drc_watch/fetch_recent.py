"""Small file helpers and seven daily quotas for X recent search."""

import csv
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
HISTORY = ROOT / 'history'  # Post-free state, tracked in git so it survives between CI runs.
QUERY = ('(Goma OR "North Kivu" OR "Nord-Kivu" OR "South Kivu" OR "Sud-Kivu" '
         'OR Ituri OR M23 OR "DR Congo" OR "RD Congo" OR RDC) '
         '(attaque OR attack OR combat OR violence OR affrontement OR conflict '
         'OR displaced OR déplacés OR fighting OR bombardement OR offensive) -is:retweet')


def now():
    return datetime.now(timezone.utc)


def load_env():
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    return {**values, **os.environ}


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


def read_jsonl(path):
    # Also accepts the older project's whitespace-separated JSON objects.
    text, decoder, rows, index = Path(path).read_text(), json.JSONDecoder(), [], 0
    while index < len(text):
        if text[index].isspace():
            index += 1
        else:
            row, index = decoder.raw_decode(text, index)
            rows.append(row)
    return rows


def latest_summary():
    files = sorted((DATA / 'raw').glob('summary_*.json'))
    if not files:
        raise ValueError('No X snapshot: use pipeline.py fetch-posts or pass --posts /path/to/saved.jsonl.')
    summary = json.loads(files[-1].read_text())
    summary['posts_file'] = str(DATA / 'raw' / summary['posts_file'])
    return summary


def x_time(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def collect(token, target=1000, current=None, pause=1):
    if target < 7:
        raise ValueError('Use at least seven posts, one quota per day.')
    current = current or now()
    start, end = current - timedelta(days=7) + timedelta(minutes=2), current - timedelta(minutes=1)
    width = (end - start) / 7
    posts, buckets, fetched, stopped = {}, [], 0, None
    for day in range(7):
        quota, count, next_token = target // 7 + (day < target % 7), 0, None
        lower, upper = start + day * width, start + (day + 1) * width
        while count < quota:
            params = {'query': QUERY, 'start_time': x_time(lower), 'end_time': x_time(upper),
                      'max_results': max(10, min(100, quota - count)),
                      'tweet.fields': 'id,text,created_at,lang,author_id'}
            if next_token:
                params['next_token'] = next_token
            response = requests.get('https://api.x.com/2/tweets/search/recent', params=params,
                                    headers={'Authorization': f'Bearer {token}'}, timeout=60)
            if response.status_code in (402, 429):
                stopped = f'X HTTP {response.status_code}; partial snapshot retained'
                break
            if response.status_code != 200:
                raise ValueError(f'X HTTP {response.status_code}; check access before retrying.')
            payload = response.json()
            batch = payload.get('data', [])
            fetched += len(batch)
            for post in batch:
                if count < quota and post['id'] not in posts:
                    posts[post['id']] = post
                    count += 1
            next_token = payload.get('meta', {}).get('next_token')
            if not next_token or not batch:
                break
            time.sleep(pause)
        buckets.append({'start': lower.isoformat(), 'end': upper.isoformat(), 'quota': quota, 'returned': count})
        if stopped:
            break
        time.sleep(pause)
    return list(posts.values()), {'daily_quotas': buckets, 'fetched_before_cap': fetched, 'stopped_early': stopped}


def save_snapshot(posts, requested, details=None):
    if not posts:
        raise ValueError('No posts returned; no forecast was produced.')
    stamp = now().strftime('%Y%m%dT%H%M%S%fZ')
    path = DATA / 'raw' / f'posts_{stamp}.jsonl'
    write_jsonl(path, posts)
    summary = {'posts_file': path.name, 'sha256': checksum(path), 'retrieved_at': now().isoformat(),
               'requested': requested, 'returned': len(posts), 'query': QUERY,
               'posts_per_day': dict(sorted(Counter(p['created_at'][:10] for p in posts).items())),
               'languages': dict(Counter(p.get('lang', 'und') for p in posts)), **(details or {})}
    write_json(DATA / 'raw' / f'summary_{stamp}.json', summary)
    return {**summary, 'posts_file': str(path)}
