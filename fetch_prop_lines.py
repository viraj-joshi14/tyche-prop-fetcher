"""
fetch_prop_lines.py
Fetches NBA prop lines from PrizePicks + Underdog, uploads to S3.
Runs from GitHub Actions (not AWS EC2) so DataDome doesn't block it.
S3 output: s3://tyche-preds/predictions/nba_prop_lines.json
"""
import json, os, sys, unicodedata
from datetime import datetime, timezone

def _norm(name):
    return (unicodedata.normalize('NFD', name)
            .encode('ascii', 'ignore').decode('utf-8').lower().strip())

PP_STAT_MAP = {
    'Points':'pts','Rebounds':'reb','Assists':'ast',
    'Pts+Rebs+Asts':'pra','Points+Rebounds+Assists':'pra',
    'Pts+Rebs':'pr','Points+Rebounds':'pr',
    'Pts+Asts':'pa','Points+Assists':'pa',
    'Rebs+Asts':'ra','Rebounds+Assists':'ra',
    '3-PT Made':'3pm','3-Pointers Made':'3pm',
    'Steals':'stl','Blocked Shots':'blk','Turnovers':'to',
}

def fetch_prizepicks():
    import requests
    url = 'https://api.prizepicks.com/projections?league_id=7&per_page=500'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://app.prizepicks.com',
        'Referer': 'https://app.prizepicks.com/',
        'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-site',
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[PP] fetch failed: {e}', flush=True)
        return {}
    players = {p['id']: p for p in data.get('included', []) if p.get('type') == 'new_player'}
    lines, skipped = {}, 0
    for proj in data.get('data', []):
        if proj.get('type') != 'projection': continue
        attrs = proj.get('attributes', {})
        stat_col = PP_STAT_MAP.get(attrs.get('stat_type', ''))
        if not stat_col: skipped += 1; continue
        line_score = attrs.get('line_score')
        if line_score is None: continue
        pid = proj.get('relationships', {}).get('new_player', {}).get('data', {}).get('id')
        name = players.get(pid, {}).get('attributes', {}).get('display_name', '')
        if not name: continue
        lines[f'{name}_{stat_col}'] = float(line_score)
        lines[f'{_norm(name)}_{stat_col}'] = float(line_score)
    print(f'[PP] {len(lines)//2} players ({skipped} stat types skipped)', flush=True)
    return lines

UD_STAT_MAP = {
    'Points':'pts','Player Points':'pts','Rebounds':'reb','Player Rebounds':'reb',
    'Assists':'ast','Player Assists':'ast','Pts + Rebs + Asts':'pra',
    'Points + Rebounds':'pr','Points + Assists':'pa','Rebounds + Assists':'ra',
}
UD_SKIP = {'Spread', 'Margin of Victory', 'Total Points'}

def fetch_underdog():
    import requests
    url = 'https://api.underdogfantasy.com/beta/v3/over_under_lines?sport_id=NBA'
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
               'Origin': 'https://underdogfantasy.com', 'Referer': 'https://underdogfantasy.com/'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[UD] fetch failed: {e}', flush=True)
        return {}
    player_map = {p['id']: p for p in data.get('players', [])}
    appear_map  = {a['id']: a for a in data.get('appearances', [])}
    lines, skipped = {}, 0
    for line in data.get('over_under_lines', []):
        if line.get('status') != 'active': continue
        val = line.get('stat_value')
        if val is None: continue
        ou = line.get('over_under', {})
        app_stat = ou.get('appearance_stat', {})
        stat = app_stat.get('display_stat', '') or app_stat.get('stat', '')
        if stat in UD_SKIP: continue
        stat_col = UD_STAT_MAP.get(stat)
        if not stat_col: skipped += 1; continue
        app = appear_map.get(app_stat.get('appearance_id', ''), {})
        p = player_map.get(app.get('player_id'), {})
        if p.get('sport_id') != 'NBA': continue
        name = (p.get('first_name', '') + ' ' + p.get('last_name', '')).strip()
        if not name: continue
        lines[f'{name}_{stat_col}'] = float(val)
        lines[f'{_norm(name)}_{stat_col}'] = float(val)
    print(f'[UD] {len(lines)//2} players ({skipped} stat types skipped)', flush=True)
    return lines

def upload_to_s3(lines, source_counts):
    import boto3
    bucket = os.environ.get('S3_BUCKET', 'tyche-preds')
    key = 'predictions/nba_prop_lines.json'
    payload = {'fetched_at': datetime.now(timezone.utc).isoformat(),
               'player_count': len(lines)//2, 'source_counts': source_counts, 'lines': lines}
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(payload), ContentType='application/json')
    print(f'[S3] Uploaded {len(lines)//2} players -> s3://{bucket}/{key}', flush=True)

def main():
    print('=== Tyche Prop Line Fetcher ===', flush=True)
    pp_lines = fetch_prizepicks()
    ud_lines = fetch_underdog()
    combined = {**ud_lines, **pp_lines}
    counts = {'prizepicks': len(pp_lines)//2, 'underdog': len(ud_lines)//2, 'combined': len(combined)//2}
    print(f'[merge] PP={counts["prizepicks"]} UD={counts["underdog"]} combined={counts["combined"]}', flush=True)
    if not combined:
        print('ERROR: No lines fetched -- not uploading', flush=True)
        sys.exit(1)
    upload_to_s3(combined, counts)
    print('Done.', flush=True)

if __name__ == '__main__':
    main()
